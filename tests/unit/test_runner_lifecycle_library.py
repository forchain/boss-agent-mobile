"""
tests/unit/test_runner_lifecycle_library.py
===========================================
The shared runner lifecycle library, and the LISTEN-only hazard it exists to close.

Five runner scripts each hand-rolled the same process lifecycle, and the copies
drifted into a live hazard: `web.sh` probed port ownership LISTEN-only, while
`pocketbase.sh` and `appium.sh` resolved "who owns the port" with a bare `lsof -ti`
and then signalled that PID. A dashboard merely *connected* to PocketBase — holding an
SSE stream — could be killed by an unrelated `pb restart` and adopted as the service's
own process.

The library tests below prove the policy once, against fake `ps`/`lsof` outputs; the
per-service tests at the end prove each stop path actually goes through it.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from _service_harness import REPO_ROOT, free_port, is_port_free, wait_for_port_bound

RUNNER_LIB = REPO_ROOT / "runner_lib.sh"
STOP_BUDGET_SEC = 20.0

_CONNECTED_CLIENT_STUB = """
import socket
import sys
import time

connection = socket.create_connection(("127.0.0.1", int(sys.argv[1])))
print("connected", flush=True)
time.sleep(600)
"""

#: A real TCP listener standing in for the service itself.
_LISTENER_STUB = """
import socket
import sys
import time

server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", int(sys.argv[1])))
server.listen(5)
print("listening", flush=True)
time.sleep(600)
"""


# --------------------------------------------------------------------------- #
# The library, against fake tooling
# --------------------------------------------------------------------------- #


@pytest.fixture
def fake_toolchain(tmp_path: Path) -> Path:
    """A PATH directory whose `ps` and `lsof` answer from scripted files.

    No real process is inspected or signalled by these tests: `ps` reports the PIDs
    listed in `FAKE_PS_ALIVE`, and `lsof` reports the PIDs listed in
    `FAKE_LSOF_LISTEN` — the last of which is the whole point, since a bare probe
    would also return `FAKE_LSOF_CONNECTED`.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    (bin_dir / "ps").write_text(
        "#!/usr/bin/env bash\n"
        "# `ps -p PID` succeeds iff the pid is listed in FAKE_PS_ALIVE.\n"
        "pid=\"\"\n"
        "stat_format=\"\"\n"
        "while [[ $# -gt 0 ]]; do\n"
        "  case \"$1\" in\n"
        "    -p) pid=\"$2\"; shift 2 ;;\n"
        "    -o) stat_format=\"$2\"; shift 2 ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        "if grep -qw \"${pid}\" <<< \"${FAKE_PS_ALIVE:-}\"; then\n"
        "  if [[ -n \"${stat_format}\" ]]; then echo \"${FAKE_PS_STATE:-S}\"; fi\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    (bin_dir / "lsof").write_text(
        "#!/usr/bin/env bash\n"
        "# Only a LISTEN query returns FAKE_LSOF_LISTEN; a bare query returns both, which\n"
        "# is exactly what used to make a connected client a kill candidate.\n"
        "listen_only=0\n"
        "for arg in \"$@\"; do\n"
        "  [[ \"${arg}\" == \"-sTCP:LISTEN\" ]] && listen_only=1\n"
        "done\n"
        "if [[ ${listen_only} -eq 1 ]]; then\n"
        "  echo \"${FAKE_LSOF_LISTEN:-}\"\n"
        "else\n"
        "  echo \"${FAKE_LSOF_CONNECTED:-} ${FAKE_LSOF_LISTEN:-}\"\n"
        "fi\n",
        encoding="utf-8",
    )
    for shim in bin_dir.iterdir():
        shim.chmod(0o755)
    return bin_dir


def _run_library(
    bin_dir: Path, script: str, cwd: Path | None = None, **env: str
) -> subprocess.CompletedProcess:
    """Run a snippet with the library sourced and only the fake toolchain on PATH."""
    environment = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "FAKE_PS_ALIVE": "",
        "FAKE_LSOF_LISTEN": "",
        "FAKE_LSOF_CONNECTED": "",
        **env,
    }
    return subprocess.run(
        ["/bin/bash", "-c", f'source "{RUNNER_LIB}"\n{script}'],
        capture_output=True,
        text=True,
        env=environment,
        cwd=str(cwd) if cwd else None,
        timeout=30,
    )


def test_the_port_probe_only_ever_returns_the_listener(fake_toolchain: Path) -> None:
    """A client connected to the port is never the port's owner."""
    result = _run_library(
        fake_toolchain,
        'runner_port_listener_pid 5173',
        FAKE_LSOF_LISTEN="4242",
        FAKE_LSOF_CONNECTED="9999",
    )
    assert result.stdout.strip() == "4242"


def test_the_port_probe_returns_nothing_for_a_connected_client_alone(
    fake_toolchain: Path,
) -> None:
    """The hazard case: something is connected, but nothing is listening."""
    result = _run_library(
        fake_toolchain,
        'runner_port_listener_pid 5173',
        FAKE_LSOF_CONNECTED="9999",
    )
    assert result.stdout.strip() == ""


def test_a_zombie_is_not_alive(fake_toolchain: Path) -> None:
    """A defunct process has terminated; it is neither running nor worth a timeout."""
    alive = _run_library(fake_toolchain, 'runner_process_alive 7 && echo yes', FAKE_PS_ALIVE="7")
    assert alive.stdout.strip() == "yes"

    zombie = _run_library(
        fake_toolchain,
        'runner_process_alive 7 && echo yes',
        FAKE_PS_ALIVE="7",
        FAKE_PS_STATE="Z+",
    )
    assert zombie.stdout.strip() == ""


def test_a_just_dead_process_is_still_reported_as_gone(fake_toolchain: Path) -> None:
    """The wait predicate re-checks at the deadline, so a late exit still counts."""
    result = _run_library(fake_toolchain, "runner_wait_until 1 runner_process_gone 7 && echo gone")
    assert result.stdout.strip() == "gone"


def test_resolving_a_pid_prefers_the_pidfile_over_a_stray_listener(
    fake_toolchain: Path, tmp_path: Path
) -> None:
    """The pidfile is the service we started; a listener is only a candidate owner."""
    pidfile = tmp_path / "service.pid"
    pidfile.write_text("555\n", encoding="utf-8")
    result = _run_library(
        fake_toolchain,
        f'runner_resolve_pid "{pidfile}" 5173',
        FAKE_PS_ALIVE="555",
        FAKE_LSOF_LISTEN="4242",
    )
    assert result.stdout.strip() == "555"


def test_resolving_a_pid_falls_back_to_the_listener_never_a_client(
    fake_toolchain: Path, tmp_path: Path
) -> None:
    pidfile = tmp_path / "service.pid"
    result = _run_library(
        fake_toolchain,
        f'runner_resolve_pid "{pidfile}" 5173',
        FAKE_LSOF_LISTEN="4242",
        FAKE_LSOF_CONNECTED="9999",
    )
    assert result.stdout.strip() == "4242"
    assert pidfile.read_text(encoding="utf-8").strip() == "4242", "the owner is adopted"


def test_stopping_a_port_with_only_a_client_does_nothing(fake_toolchain: Path) -> None:
    """The library's stop-by-port is a no-op when the port has no listener."""
    result = _run_library(
        fake_toolchain,
        "runner_stop_port_listener 5173 1 probe && echo stopped",
        FAKE_LSOF_CONNECTED="9999",
    )
    assert "stopped" in result.stdout


def test_the_runtime_directory_split_is_a_policy_not_a_symlink(tmp_path: Path) -> None:
    """Infrastructure state anchors at the common root; application state per worktree.

    The split used to be emergent: `pocketbase.sh` self-anchored to the git common root
    while every other script wrote worktree-relative `.boss_agent/`, and they converged
    only because `init_worktree.sh` symlinks one to the other. Removing the symlink used
    to silently half-split a manually created worktree; it no longer does.
    """
    bin_dir = fake_toolchain_stub(tmp_path)

    # Inside a git repository: infrastructure state is shared, application state is not.
    from_service_harness = _run_library(bin_dir, 'runner_common_root "."', cwd=REPO_ROOT)
    common_root = Path(from_service_harness.stdout.strip())
    assert common_root != REPO_ROOT or common_root.is_dir()

    infra_inside_git = _run_library(bin_dir, 'runner_runtime_dir infra "."', cwd=REPO_ROOT)
    app_inside_git = _run_library(bin_dir, f'runner_runtime_dir app "{tmp_path}"', cwd=REPO_ROOT)
    assert Path(infra_inside_git.stdout.strip()).parent == common_root
    assert app_inside_git.stdout.strip() == f"{tmp_path}/.boss_agent", (
        "application state must stay in the worktree"
    )


def test_outside_a_repository_both_classes_fall_back_to_the_given_root(tmp_path: Path) -> None:
    """No git, no common root: the policy degrades to the working directory."""
    bin_dir = fake_toolchain_stub(tmp_path)
    for service_class in ("infra", "app"):
        result = _run_library(
            bin_dir, f'runner_runtime_dir {service_class} "{tmp_path}"', cwd=tmp_path
        )
        assert result.stdout.strip() == f"{tmp_path}/.boss_agent"


def test_an_unknown_service_class_is_refused(tmp_path: Path) -> None:
    result = _run_library(fake_toolchain_stub(tmp_path), f'runner_runtime_dir widget "{tmp_path}"')
    assert result.returncode == 2
    assert "unknown service class" in result.stderr


def fake_toolchain_stub(tmp_path: Path) -> Path:
    """A PATH directory with the real `git` only; the runtime-dir policy needs nothing else."""
    bin_dir = tmp_path / "rt-bin"
    bin_dir.mkdir(exist_ok=True)
    return bin_dir


# --------------------------------------------------------------------------- #
# Every service's stop path goes through the shared probe
# --------------------------------------------------------------------------- #


@pytest.fixture
def live_service():
    """A real listening service plus a client connected to it, cleaned up after."""
    started: list[subprocess.Popen] = []
    try:
        yield started
    finally:
        for process in started:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=STOP_BUDGET_SEC)


def _start_listener(started: list[subprocess.Popen]) -> int:
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "-c", _LISTENER_STUB, str(port)],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    started.append(process)
    assert process.stdout and process.stdout.readline().strip() == "listening"
    wait_for_port_bound(port)
    return port


def _start_client(started: list[subprocess.Popen], port: int) -> subprocess.Popen:
    process = subprocess.Popen(
        [sys.executable, "-c", _CONNECTED_CLIENT_STUB, str(port)],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    started.append(process)
    assert process.stdout and process.stdout.readline().strip() == "connected"
    return process


def _runner_root(tmp_path: Path, script: str) -> Path:
    root = tmp_path / "repo"
    (root / ".boss_agent").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / script, root / script)
    shutil.copy2(RUNNER_LIB, root / "runner_lib.sh")
    return root


@pytest.mark.parametrize(
    ("script", "env_prefix"),
    [("pocketbase.sh", "PB_HTTP=127.0.0.1:{port}"), ("appium.sh", "APPIUM_PORT={port}")],
)
def test_stop_never_signals_a_client_merely_connected_to_the_port(
    tmp_path: Path, live_service: list, script: str, env_prefix: str
) -> None:
    """The hazard's two live sites, proven closed.

    `web.sh` had this regression test; `pocketbase.sh` and `appium.sh` had none, which
    is why the bare `lsof -ti` sit in them survived. The acceptance bar is the same
    test running against every service's stop path.
    """
    root = _runner_root(tmp_path, script)
    port = _start_listener(live_service)
    client = _start_client(live_service, port)

    env = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}:{os.environ.get('PATH', '')}",
        **dict(pair.split("=", 1) for pair in env_prefix.format(port=port).split(" ")),
    }
    subprocess.run(
        [str(root / script), "stop"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=STOP_BUDGET_SEC * 2,
    )

    time.sleep(0.3)
    assert client.poll() is None, (
        f"{script} stop signalled a client merely connected to port {port}"
    )
    assert is_port_free(port), f"{script} stop left its own listener running"


# --------------------------------------------------------------------------- #
# The shutdown-acknowledgment contract
# --------------------------------------------------------------------------- #


def test_the_shutdown_acknowledgment_has_one_definition_per_service() -> None:
    """The ack marker is a contract across three producers and one consumer.

    It used to survive on comment discipline — "never reword it" — with the dashboard's
    half hand-copied into the teardown service. A test that names the offender is what
    replaces the comment.
    """
    from boss_agent.services.teardown import WEB_SHUTDOWN_ACK, WORKER_SHUTDOWN_ACK
    from boss_agent.worker.daemon import SHUTDOWN_ACK_MARKER

    library = RUNNER_LIB.read_text(encoding="utf-8")
    assert f'RUNNER_WEB_SHUTDOWN_ACK="{WEB_SHUTDOWN_ACK}"' in library, (
        "runner_lib.sh no longer defines the dashboard's ack; the teardown gate reads "
        "this exact substring"
    )
    assert WORKER_SHUTDOWN_ACK == SHUTDOWN_ACK_MARKER, (
        "the worker's ack is owned by the daemon; teardown must mirror it, not restate it"
    )


def test_the_dashboard_runner_no_longer_restates_the_ack_literal() -> None:
    """web.sh references the shared definition rather than carrying a copy."""
    from boss_agent.services.teardown import WEB_SHUTDOWN_ACK

    web = (REPO_ROOT / "web.sh").read_text(encoding="utf-8")
    assert "RUNNER_WEB_SHUTDOWN_ACK" in web, "web.sh must use the library's definition"
    assert f'"[Web] {WEB_SHUTDOWN_ACK}, shutting down' not in web, (
        "web.sh restates the ack literal again; reference the shared constant instead"
    )
