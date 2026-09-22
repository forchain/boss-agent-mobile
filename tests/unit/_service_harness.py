"""
tests/unit/_service_harness.py
==============================
Shared process/port plumbing for the service-lifecycle tests (spec #218, tickets #220-#222).

Not a test module (no `test_` prefix, so pytest never collects it): these helpers are
imported by the Worker CLI, Web Dashboard runner, and E2E gate suites so that port
allocation, log polling, process reaping, and subprocess environments are defined once.
"""

import os
import socket
import subprocess
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def free_port() -> int:
    """Reserve and release a port, returning a number that was free a moment ago."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def is_port_free(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def wait_for_port_bound(port: int, timeout: float = 10.0) -> None:
    """Block until something listens on `port`, failing the test if nothing does."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_port_free(port):
            return
        time.sleep(0.05)
    raise AssertionError(f"stub server never bound port {port}")


def wait_for_log(log_file: Path, marker: str, timeout: float = 30.0) -> None:
    """Block until `marker` appears in `log_file`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log_file.exists() and marker in log_file.read_text(encoding="utf-8", errors="replace"):
            return
        time.sleep(0.05)
    raise AssertionError(f"never observed {marker!r} in {log_file}")


def is_alive(pid: int) -> bool:
    """A plain liveness check; unlike the production paths this does not special-case zombies."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def wait_until_dead(process: subprocess.Popen, timeout: float = 5.0) -> bool:
    """Wait for a stub process to terminate, reaping it so it cannot linger as a zombie."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return True
        time.sleep(0.05)
    return process.poll() is not None


def wait_until_dead_pid(pid: int, timeout: float = 5.0) -> bool:
    """Wait for a process that is not our child (so cannot be reaped via Popen.poll)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_alive(pid):
            return True
        time.sleep(0.05)
    return not is_alive(pid)


def subprocess_env(*, strip: Sequence[str] = (), **overrides: str) -> dict[str, str]:
    """Environment for child processes: this repo importable, output unbuffered.

    `strip` removes variables inherited from the parent run (applied before `overrides`, so
    a value can still be set explicitly), and `overrides` are appended last.
    """
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in [str(REPO_ROOT / "src"), str(REPO_ROOT), existing] if part
    )
    env["PYTHONUNBUFFERED"] = "1"
    for name in strip:
        env.pop(name, None)
    env.update({key: str(value) for key, value in overrides.items()})
    return env


@pytest.fixture
def spawn() -> Iterator[Callable[..., subprocess.Popen]]:
    """Factory for detached stub processes, all reaped at teardown.

    Pass `expect_stdout` to wait for a readiness line the stub prints once it is set up.
    """
    started: list[subprocess.Popen] = []

    def _spawn(
        argv: list[str],
        *,
        expect_stdout: str | None = None,
        stdout=None,
        stderr=None,
        env: dict[str, str] | None = None,
    ) -> subprocess.Popen:
        if expect_stdout is not None:
            process = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                env=env,
                start_new_session=True,
            )
            assert process.stdout is not None
            reported = process.stdout.readline().strip()
            assert reported == expect_stdout, (
                f"stub reported {reported!r}, expected {expect_stdout!r}"
            )
        else:
            process = subprocess.Popen(
                argv,
                stdout=stdout if stdout is not None else subprocess.DEVNULL,
                stderr=stderr if stderr is not None else subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )
        started.append(process)
        return process

    yield _spawn

    for process in started:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
