"""
tests/unit/test_service_teardown_gate.py
========================================
Unit tests for the E2E pre-test teardown gate (spec #218, ticket #222).

Every scenario runs against a temporary repository root containing a copy of the real
`web.sh`, so the gate is exercised end to end without signalling the shared Web Dashboard
or Automation Worker belonging to other worktrees.
"""

import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from _service_harness import (
    free_port,
    is_alive,
    wait_for_log,
    wait_for_port_bound,
    wait_until_dead,
    wait_until_dead_pid,
)
from _service_harness import (
    spawn as spawn,  # noqa: PLC0414 - re-exported so pytest discovers the fixture
)

from boss_agent.services.teardown import ServiceTeardownGate, TeardownGateError

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GATE_TIMEOUT_SEC = 3.0

SERVICE_STUB = """
import signal
import sys
import time

log_path, mode = sys.argv[1], sys.argv[2]


def _on_sigterm(signum, frame):
    if mode == "silent":
        sys.exit(0)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write("2026-01-01 00:00:00 [INFO] boss_agent.worker: "
                     "\\U0001F6D1 [Worker] Received shutdown signal (SIGTERM), "
                     "initiating graceful shutdown...\\n")
        handle.write("2026-01-01 00:00:00 [INFO] boss_agent.worker: "
                     "\\U0001F44B [Worker] Shutdown complete.\\n")
    sys.exit(0)


signal.signal(signal.SIGTERM, _on_sigterm)
if mode == "ignore":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

# Logged only after the handlers are in place: the test waits for this before signalling.
with open(log_path, "a", encoding="utf-8") as handle:
    handle.write("2026-01-01 00:00:00 [INFO] boss_agent.worker: worker loop started\\n")

while True:
    time.sleep(0.05)
"""

WORKER_READY_MARKER = "worker loop started"

# Mirrors `uv run python3 scripts/worker.py`: the recorded PID is a launcher whose child is
# the real Automation Worker process.
LAUNCHER_STUB = """
import subprocess
import sys
import time

log_path, mode, child_pid_file, stub_source = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
child = subprocess.Popen([sys.executable, "-c", stub_source, log_path, mode])
with open(child_pid_file, "w", encoding="utf-8") as handle:
    handle.write(str(child.pid))

while True:
    time.sleep(0.05)
"""

CONNECTED_CLIENT_STUB = """
import socket
import sys
import time

connection = socket.create_connection(("127.0.0.1", int(sys.argv[1])))
print("connected", flush=True)
time.sleep(600)
"""


@pytest.fixture
def runtime_root(tmp_path: Path) -> Path:
    """Throwaway repo root carrying a copy of web.sh plus its own .boss_agent runtime dir."""
    root = tmp_path / "repo"
    (root / ".boss_agent").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "web.sh", root / "web.sh")
    return root


def _spawn_worker_stub(
    spawn, runtime_root: Path, mode: str = "graceful", pid_file: str = "worker.pid"
) -> tuple[subprocess.Popen, Path]:
    log_file = runtime_root / ".boss_agent" / "worker.log"
    process = spawn(
        [sys.executable, "-c", SERVICE_STUB, str(log_file), mode],
    )
    worker_pid_file = runtime_root / ".boss_agent" / pid_file
    worker_pid_file.write_text(str(process.pid), encoding="utf-8")
    # Only signal once the stub has installed its handlers, as a real worker would have.
    wait_for_log(log_file, WORKER_READY_MARKER)
    return process, log_file


def _gate(runtime_root: Path, **overrides) -> ServiceTeardownGate:
    # Always target a throwaway port: the real dashboard port may be in use by another worktree.
    overrides.setdefault("web_port", free_port())
    return ServiceTeardownGate(
        repo_root=runtime_root,
        worker_stop_timeout_sec=GATE_TIMEOUT_SEC,
        web_stop_timeout_sec=GATE_TIMEOUT_SEC,
        **overrides,
    )


def test_gate_is_a_noop_when_no_services_are_running(runtime_root: Path):
    """Verify an already-clean environment costs effectively nothing."""
    started_at = time.monotonic()
    report = _gate(runtime_root).enforce()
    elapsed = time.monotonic() - started_at

    assert elapsed < 1.0, f"idle gate took {elapsed:.2f}s"
    assert not (runtime_root / ".boss_agent" / "web.log").exists()
    assert all("stopped" not in line for line in report), report


def test_gate_stops_worker_and_verifies_shutdown_log(runtime_root: Path, spawn):
    """Verify a detected Automation Worker is signalled, verified, and cleaned up."""
    process, log_file = _spawn_worker_stub(spawn, runtime_root)

    report = _gate(runtime_root).enforce()

    assert wait_until_dead(process), "worker stub survived the gate"
    assert process.poll() == 0, "worker stub was not terminated gracefully"
    log_contents = log_file.read_text(encoding="utf-8")
    assert ServiceTeardownGate.WORKER_SHUTDOWN_ACK in log_contents, log_contents
    assert not (runtime_root / ".boss_agent" / "worker.pid").exists()
    assert any("worker" in line.lower() for line in report), report


def test_gate_force_kills_worker_that_ignores_sigterm(runtime_root: Path, spawn):
    """Verify a wedged worker is still removed, and the missing acknowledgment is reported."""
    process, _ = _spawn_worker_stub(spawn, runtime_root, mode="ignore")

    with pytest.raises(TeardownGateError) as excinfo:
        _gate(runtime_root).enforce()

    assert wait_until_dead(process), "SIGTERM-immune worker survived the gate"
    assert ServiceTeardownGate.WORKER_SHUTDOWN_ACK in str(excinfo.value)


def test_gate_reports_worker_that_exits_without_shutdown_feedback(runtime_root: Path, spawn):
    """Verify a legacy worker (exits silently) fails the gate with an actionable message."""
    process, _ = _spawn_worker_stub(spawn, runtime_root, mode="silent")

    with pytest.raises(TeardownGateError) as excinfo:
        _gate(runtime_root).enforce()

    assert wait_until_dead(process)
    assert "worker.log" in str(excinfo.value)


def test_gate_stops_worker_spawned_through_a_launcher(runtime_root: Path, spawn):
    """Verify the real worker behind a launcher process is stopped, not just the launcher."""
    log_file = runtime_root / ".boss_agent" / "worker.log"
    child_pid_file = runtime_root / ".boss_agent" / "worker_child.pid"
    launcher = spawn(
        [
            sys.executable,
            "-c",
            LAUNCHER_STUB,
            str(log_file),
            "graceful",
            str(child_pid_file),
            SERVICE_STUB,
        ],
    )
    (runtime_root / ".boss_agent" / "worker.pid").write_text(str(launcher.pid), encoding="utf-8")
    wait_for_log(log_file, WORKER_READY_MARKER)

    report = _gate(runtime_root).enforce()

    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    assert wait_until_dead(launcher), "launcher survived the gate"
    assert wait_until_dead_pid(child_pid), (
        "the Automation Worker behind the launcher survived the gate; the device session "
        "would still be claimed"
    )
    assert ServiceTeardownGate.WORKER_SHUTDOWN_ACK in log_file.read_text(encoding="utf-8")
    assert any("worker" in line.lower() for line in report), report


def test_gate_stops_web_dashboard_and_frees_port(runtime_root: Path, spawn):
    """Verify a detected Web Dashboard is stopped through web.sh and port release is verified."""
    port = free_port()
    server = spawn(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
    )
    wait_for_port_bound(port)
    (runtime_root / ".boss_agent" / "web.pid").write_text(str(server.pid), encoding="utf-8")

    report = _gate(runtime_root, web_port=port).enforce()

    assert wait_until_dead(server), "web stub survived the gate"
    web_log = (runtime_root / ".boss_agent" / "web.log").read_text(encoding="utf-8")
    assert ServiceTeardownGate.WEB_SHUTDOWN_ACK in web_log, web_log
    with socket.socket() as sock:
        sock.settimeout(0.5)
        assert sock.connect_ex(("127.0.0.1", port)) != 0, f"port {port} still occupied"
    assert any("web" in line.lower() for line in report), report


def test_gate_never_touches_shared_infrastructure(runtime_root: Path, spawn):
    """Verify PocketBase, Appium, and the emulator are never signalled by the gate."""
    shared_pids = {}
    for service in ("pocketbase", "appium", "emulator"):
        stub = spawn([sys.executable, "-c", "import time; time.sleep(600)"])
        shared_pids[service] = stub.pid
        (runtime_root / ".boss_agent" / f"{service}.pid").write_text(
            str(stub.pid), encoding="utf-8"
        )

    _gate(runtime_root).enforce()

    for service, pid in shared_pids.items():
        assert is_alive(pid), f"gate signalled shared infrastructure: {service} (PID {pid})"
        assert (runtime_root / ".boss_agent" / f"{service}.pid").exists(), (
            f"gate removed the PID file of shared infrastructure: {service}"
        )


def test_gate_never_signals_a_client_connected_to_the_dashboard_port(runtime_root: Path, spawn):
    """Verify the gate stops the listener without killing clients on the dashboard port."""
    port = free_port()
    server = spawn([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"])
    wait_for_port_bound(port)
    client = spawn(
        [sys.executable, "-c", CONNECTED_CLIENT_STUB, str(port)], expect_stdout="connected"
    )
    (runtime_root / ".boss_agent" / "web.pid").write_text(str(server.pid), encoding="utf-8")

    _gate(runtime_root, web_port=port).enforce()

    assert wait_until_dead(server), "dashboard listener survived the gate"
    assert client.poll() is None, (
        "gate signalled a process merely connected to the dashboard port "
        "(a browser, curl, or the E2E client itself)"
    )
