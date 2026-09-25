"""
tests/unit/test_web_runner_shutdown.py
======================================
Integration tests for the Web Dashboard teardown seam (spec #218, ticket #221).

`web.sh` is copied into a temporary runtime root so `cmd_stop` operates on throwaway
`.boss_agent/web.pid` and `.boss_agent/web.log` files — the shared Web Dashboard used by
other worktrees is never signalled by this suite.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from _service_harness import REPO_ROOT, free_port, is_port_free, wait_for_port_bound

WEB_SH = REPO_ROOT / "web.sh"
SHUTDOWN_RECORD = "🛑 [Web] Received stop command, shutting down Web Dashboard..."
SHUTDOWN_COMPLETE_RECORD = "Web Dashboard stopped"
STOP_BUDGET_SEC = 20.0

_SIGTERM_IMMUNE_SERVER = """
import signal, socket, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", {port}))
server.listen(5)
time.sleep(600)
"""

# Forking happens in a dedicated single-threaded helper: the pytest process itself is
# multi-threaded, where fork() is deprecated and unsafe.
_DEFUNCT_CHILD_HELPER = """
import os
import time

child_pid = os.fork()
if child_pid == 0:
    os._exit(0)

print(child_pid, flush=True)
time.sleep(600)
"""

_CONNECTED_CLIENT_STUB = """
import socket
import sys
import time

connection = socket.create_connection(("127.0.0.1", int(sys.argv[1])))
print("connected", flush=True)
time.sleep(600)
"""


@pytest.fixture
def web_runtime(tmp_path: Path) -> Path:
    """A throwaway repository root containing a copy of the real web.sh."""
    runtime_root = tmp_path / "repo"
    (runtime_root / ".boss_agent").mkdir(parents=True)
    shutil.copy2(WEB_SH, runtime_root / "web.sh")
    return runtime_root


@pytest.fixture
def dummy_server():
    """Yields a factory launching a real TCP listener on a free port, cleaned up after."""
    started: list[subprocess.Popen] = []

    def _start(*, ignore_sigterm: bool = False) -> tuple[subprocess.Popen, int]:
        port = free_port()
        if ignore_sigterm:
            process = subprocess.Popen(
                [sys.executable, "-c", _SIGTERM_IMMUNE_SERVER.format(port=port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            process = subprocess.Popen(
                [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        started.append(process)
        wait_for_port_bound(port)
        return process, port

    yield _start

    for process in started:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=STOP_BUDGET_SEC)


@pytest.fixture
def defunct_pid():
    """PID of a terminated process whose parent never reaps it — i.e. a defunct/zombie PID."""
    helper = subprocess.Popen(
        [sys.executable, "-c", _DEFUNCT_CHILD_HELPER],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        reported = helper.stdout.readline().strip() if helper.stdout else ""
        assert reported.isdigit(), f"helper did not report a child PID: {reported!r}"
        time.sleep(0.3)  # let the child terminate so it settles into the defunct state
        yield int(reported)
    finally:
        helper.kill()
        helper.wait(timeout=STOP_BUDGET_SEC)


@pytest.fixture
def connected_client():
    """A client process holding a live connection to `port` (a browser/curl stand-in)."""
    started: list[subprocess.Popen] = []

    def _connect(port: int) -> subprocess.Popen:
        process = subprocess.Popen(
            [sys.executable, "-c", _CONNECTED_CLIENT_STUB, str(port)],
            stdout=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        started.append(process)
        assert process.stdout and process.stdout.readline().strip() == "connected"
        return process

    yield _connect

    for process in started:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=STOP_BUDGET_SEC)


def _run_web_stop(runtime_root: Path, port: int) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["WEB_HOST"] = "127.0.0.1"
    env["WEB_PORT"] = str(port)
    # Keep the graceful→forceful escalation exercised without paying the 10s production budget twice.
    env["WEB_STOP_TIMEOUT_SEC"] = "2"
    bash = shutil.which("bash") or "/bin/bash"
    return subprocess.run(
        [bash, "web.sh", "stop"],
        cwd=str(runtime_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=STOP_BUDGET_SEC,
    )


@pytest.mark.skipif(shutil.which("lsof") is None, reason="lsof is required to verify port release")
def test_stop_never_signals_a_client_connected_to_the_port(
    web_runtime: Path, dummy_server, connected_client
):
    """Verify only the listener is stopped — a client on the port must survive."""
    server, port = dummy_server()
    client = connected_client(port)
    (web_runtime / ".boss_agent" / "web.pid").write_text(str(server.pid), encoding="utf-8")

    result = _run_web_stop(web_runtime, port)

    assert result.returncode == 0, f"stop failed:\n{result.stdout}\n{result.stderr}"
    assert server.poll() is not None, "the dashboard listener was not stopped"
    assert client.poll() is None, (
        "stop signalled a process that was merely connected to the port "
        "(a browser, curl, or the E2E client itself)"
    )


@pytest.mark.skipif(shutil.which("lsof") is None, reason="lsof is required to verify port release")
def test_stop_records_shutdown_log_and_releases_port(web_runtime: Path, dummy_server):
    """Verify stopping a running instance logs the shutdown record and frees the port."""
    process, port = dummy_server()
    pid_file = web_runtime / ".boss_agent" / "web.pid"
    log_file = web_runtime / ".boss_agent" / "web.log"
    pid_file.write_text(str(process.pid), encoding="utf-8")

    result = _run_web_stop(web_runtime, port)

    assert result.returncode == 0, f"stop failed:\n{result.stdout}\n{result.stderr}"
    assert log_file.exists(), "stop must record feedback in the web log stream"
    log_contents = log_file.read_text(encoding="utf-8", errors="replace")
    assert SHUTDOWN_RECORD in log_contents, f"missing shutdown record; log:\n{log_contents}"
    assert SHUTDOWN_COMPLETE_RECORD in log_contents, (
        f"missing completion record; log:\n{log_contents}"
    )
    assert not pid_file.exists(), "PID_FILE must be removed once the process is gone"
    assert process.poll() is not None, "web process is still alive after stop"
    assert is_port_free(port), f"port {port} is still occupied after stop"


@pytest.mark.skipif(shutil.which("lsof") is None, reason="lsof is required to verify port release")
def test_stop_escalates_to_forceful_termination_when_graceful_times_out(
    web_runtime: Path, dummy_server
):
    """Verify a SIGTERM-immune process is still released so the port never stays occupied."""
    process, port = dummy_server(ignore_sigterm=True)
    (web_runtime / ".boss_agent" / "web.pid").write_text(str(process.pid), encoding="utf-8")

    result = _run_web_stop(web_runtime, port)

    assert result.returncode == 0, f"stop failed:\n{result.stdout}\n{result.stderr}"
    assert process.poll() is not None, "SIGTERM-immune process survived the stop routine"
    assert is_port_free(port), f"port {port} is still occupied after forceful termination"


@pytest.mark.skipif(shutil.which("lsof") is None, reason="lsof is required to verify port release")
def test_stop_releases_orphaned_port_without_pid_file(web_runtime: Path, dummy_server):
    """Verify stop reclaims a port held by an orphan process that left no PID_FILE behind."""
    process, port = dummy_server()
    pid_file = web_runtime / ".boss_agent" / "web.pid"
    assert not pid_file.exists()

    result = _run_web_stop(web_runtime, port)

    assert result.returncode == 0, f"stop failed:\n{result.stdout}\n{result.stderr}"
    assert process.poll() is not None, "orphaned process holding the port was not stopped"
    assert is_port_free(port), f"port {port} is still occupied after stop"


def test_stop_treats_defunct_process_as_not_running(web_runtime: Path, defunct_pid: int):
    """Verify a stale PID_FILE pointing at a defunct process neither stalls nor misreports."""
    pid_file = web_runtime / ".boss_agent" / "web.pid"
    pid_file.write_text(str(defunct_pid), encoding="utf-8")

    started_at = time.monotonic()
    result = _run_web_stop(web_runtime, free_port())
    elapsed = time.monotonic() - started_at

    assert result.returncode == 0, f"stop failed:\n{result.stdout}\n{result.stderr}"
    assert elapsed < 1.0, (
        f"stop waited {elapsed:.2f}s on a defunct process instead of finishing immediately"
    )
    assert "No running Web Dashboard process found" in result.stdout
    assert not pid_file.exists(), "stale PID_FILE must be cleaned up"


def test_stop_is_a_cheap_noop_when_nothing_is_running(web_runtime: Path):
    """Verify an already-clean environment returns immediately without spurious log noise."""
    port = free_port()
    log_file = web_runtime / ".boss_agent" / "web.log"

    started_at = time.monotonic()
    result = _run_web_stop(web_runtime, port)
    elapsed = time.monotonic() - started_at

    assert result.returncode == 0, f"stop failed:\n{result.stdout}\n{result.stderr}"
    assert "No running Web Dashboard process found" in result.stdout
    assert elapsed < 2.0, f"idle stop took {elapsed:.2f}s; teardown gate needs a fast no-op"
    assert not log_file.exists(), "an idle stop must not fabricate shutdown records"


def test_restart_stops_existing_server_and_reclaims_port(web_runtime: Path, dummy_server):
    """Verify restart terminates any existing process holding the port before attempting start."""
    process, port = dummy_server()
    pid_file = web_runtime / ".boss_agent" / "web.pid"
    pid_file.write_text(str(process.pid), encoding="utf-8")

    env = dict(os.environ)
    env["WEB_HOST"] = "127.0.0.1"
    env["WEB_PORT"] = str(port)
    env["WEB_STOP_TIMEOUT_SEC"] = "2"
    bash = shutil.which("bash") or "/bin/bash"

    result = subprocess.run(
        [bash, "web.sh", "restart"],
        cwd=str(web_runtime),
        env=env,
        capture_output=True,
        text=True,
        timeout=STOP_BUDGET_SEC,
    )

    # Process holding the port must be dead
    assert process.poll() is not None, "process holding port survived web.sh restart"
    assert "Stopping SvelteKit Web Dashboard" in result.stdout
    assert "Restarting SvelteKit Web Dashboard" in result.stdout

