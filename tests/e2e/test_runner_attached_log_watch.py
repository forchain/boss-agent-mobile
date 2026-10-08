"""
tests/e2e/test_runner_attached_log_watch.py
============================================
An attached log stream stops following when the service it follows stops.

`runner_attached_logs` ended in a bare `tail -n 30 -f "${LOG_FILE}"`, and so does the
foreground start path of `worker.sh`. `tail -f` has no reason to ever exit: it follows a
*file*, and a daemon stopped from another terminal neither truncates, rotates, nor removes
its log. The operator was therefore left staring at a live cursor on an inert file, unable
to tell a busy Automation Worker from one killed thirty seconds ago — the "光标仍在闪烁，无
任何停止提示" failure. Ticket #425.

Two properties are asserted together, and the second is the one that is easy to lose: the
watch must notice the service is *gone*, and it must never signal it. Detaching with Ctrl+C
stops the log stream and nothing else; the daemon keeps running in the background. That is
the same LISTEN-only / never-signal discipline the runners already apply to ports, applied
here to PIDs, and it is why every test below spawns a throwaway process of its own rather
than pointing at a real service.

Why these live in the E2E tier: every one of them starts a real process and watches a real
`tail` child die. The fast unit tier rejects `subprocess.Popen` outright, which is the
right place for this hazard to be pinned — a fake `tail` cannot prove the loop returns.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from _service_harness import REPO_ROOT, free_port, wait_for_port_bound

pytestmark = pytest.mark.e2e

RUNNER_LIB = REPO_ROOT / "runner_lib.sh"
WORKER_SH = REPO_ROOT / "worker.sh"
RUN_SH = REPO_ROOT / "run.sh"

#: How long the watcher is given to notice a service that has already exited. Generous,
#: because the loop's poll interval is deliberately coarse to keep the terminal idle.
WATCH_RETURN_BUDGET_SEC = 15.0

#: How long a test lets the watcher stay attached to a *live* service before concluding it
#: gave up early. If this fires, the watch stopped following a running service.
ATTACH_HOLD_SEC = 1.5

#: The stop notice, restated here on purpose: a test states the contract it expects, while
#: the library owns the wording (see `test_the_stop_notice_has_one_definition`).
STOP_NOTICE = "🛑 [{label}] 守护进程 (PID: {pid}) 已停止"

#: The stop-and-restart notice: the same stop, plus the replacement PID that proves the
#: service was restarted rather than merely killed.
RESTART_NOTICE = "🛑 [{label}] 守护进程 (PID: {pid}) 已停止并重启 (新 PID: {new_pid})"

#: The literal three Dedicated Runner Scripts substitute when they can see their service is
#: up but could not resolve the PID owning it.
UNRESOLVED_PID = "unknown"

#: The Automation Worker's label as the Dedicated Runner Scripts pass it to the watcher.
WORKER_LABEL = "Automation Worker daemon"

#: A daemon stub that writes its readiness line and then simply stays alive. Synthetic on
#: purpose — this suite must never point a watch at a real Automation Worker, PocketBase,
#: or Virtual Device Session belonging to another worktree.
_DAEMON_STUB = """
import sys
import time

sys.stdout.write("daemon ready\\n")
sys.stdout.flush()
time.sleep(300)
"""


def _bash() -> str:
    return shutil.which("bash") or "/bin/bash"


# --------------------------------------------------------------------------- #
# Fixtures: a throwaway library root, and services the test owns outright
# --------------------------------------------------------------------------- #


@pytest.fixture
def lib_runtime(tmp_path: Path) -> Path:
    """A throwaway root holding the real `runner_lib.sh` and its own private log directory."""
    root = tmp_path / "repo"
    (root / ".boss_agent").mkdir(parents=True)
    shutil.copy2(RUNNER_LIB, root / "runner_lib.sh")
    return root


@pytest.fixture
def owned_service():
    """A factory for real, synthetic, long-lived processes; every one is reaped at teardown."""
    started: list[subprocess.Popen] = []

    def _start(seconds: float = 300.0) -> subprocess.Popen:
        process = subprocess.Popen(
            [sys.executable, "-c", f"import time; time.sleep({seconds})"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        started.append(process)
        return process

    yield _start

    for process in started:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


@pytest.fixture
def defunct_pid():
    """PID of a terminated process whose parent never reaps it — i.e. a defunct/zombie PID.

    Manufactured exactly as `test_dashboard_runner_shutdown.py` does: forking happens in a
    dedicated single-threaded helper, because fork() is deprecated and unsafe in the
    multi-threaded pytest process itself.
    """
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
        helper.wait(timeout=10)


_DEFUNCT_CHILD_HELPER = """
import os
import time

child_pid = os.fork()
if child_pid == 0:
    os._exit(0)

print(child_pid, flush=True)
time.sleep(300)
"""


# --------------------------------------------------------------------------- #
# Driving the watcher
# --------------------------------------------------------------------------- #


def _start_watch(
    runtime_root: Path,
    pid: int | str,
    log_file: Path,
    label: str = WORKER_LABEL,
    *,
    pid_file: Path | None = None,
    function: str = "runner_attached_logs",
) -> subprocess.Popen:
    """Attach to a service log in a child shell, and leave it there.

    The child gets its own session so that a signal the test sends to the watcher never
    reaches the watched service by way of a shared process group — the watcher's own
    decision to signal (or not to) is the only thing under test.

    `pid_file` is the service's own pidfile, handed to the watch so a restart from another
    terminal can be told apart from a plain stop; see `test_a_restart_from_another_terminal
    _is_announced_as_a_stop_and_restart`.
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    watcher = subprocess.Popen(
        [
            _bash(),
            "-c",
            f'source "{runtime_root / "runner_lib.sh"}"; {function} "$1" "$2" "$3" "$4" "$5"',
            "watcher",
            str(pid),
            str(log_file),
            label,
            "",  # ENDPOINT
            str(pid_file) if pid_file else "",
        ],
        cwd=str(runtime_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    _drain_output(watcher)
    _wait_for_text(watcher, "Attaching to live log stream", budget=WATCH_RETURN_BUDGET_SEC)
    return watcher


#: Everything each watcher has printed, keyed by its PID.
#:
#: One reader per pipe: `tail -f` holds the stream open for as long as it runs, so the
#: output has to be drained continuously on a background thread. Both the "did it say X yet"
#: waits and the final assertions read this buffer, which is why nothing may call
#: `communicate()` on a watcher whose drain thread is still alive.
_OUTPUT: dict[int, list[str]] = {}
_DRAINERS: dict[int, threading.Thread] = {}


def _drain_output(watcher: subprocess.Popen) -> None:
    """Start draining a watcher's output; returns immediately."""
    seen = _OUTPUT.setdefault(watcher.pid, [])
    if watcher.pid in _DRAINERS:
        return
    assert watcher.stdout is not None

    def _pump() -> None:
        for line in watcher.stdout:
            seen.append(line)

    thread = threading.Thread(target=_pump, daemon=True)
    _DRAINERS[watcher.pid] = thread
    thread.start()


def _output_of(watcher: subprocess.Popen) -> str:
    return "".join(_OUTPUT.get(watcher.pid, []))


def _wait_for_text(watcher: subprocess.Popen, text: str, *, budget: float) -> None:
    """Block until `text` has appeared on the watcher's output, or fail the test."""
    _drain_output(watcher)
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if text in _output_of(watcher):
            return
        if watcher.poll() is not None and text in _output_of(watcher):
            return
        time.sleep(0.05)
    _terminate(watcher)
    raise AssertionError(f"watcher never printed {text!r}; saw: {_output_of(watcher)!r}")


def _terminate(watcher: subprocess.Popen) -> str:
    """Take the whole watcher session down and return everything it printed.

    `killpg` is used because the `tail` child shares the watcher's process group; killing
    only the leader would leave the tail holding the output pipe open forever.
    """
    if watcher.poll() is None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(watcher.pid), signal.SIGKILL)
    watcher.wait(timeout=10)
    drainer = _DRAINERS.get(watcher.pid)
    if drainer is not None:
        drainer.join(timeout=5)
    return _output_of(watcher)


def _assert_returns(watcher: subprocess.Popen, *, budget: float = WATCH_RETURN_BUDGET_SEC) -> str:
    """Assert the watcher exited on its own, and return what it printed.

    The assertion is on *process exit*, not on printed text: the ticket's failure is a
    terminal that never returns, and a test that only read the notice would pass against a
    watcher that printed it and then hung.
    """
    _drain_output(watcher)
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if watcher.poll() is not None:
            break
        time.sleep(0.05)
    if watcher.poll() is None:
        output = _terminate(watcher)
        pytest.fail(
            f"the watcher was still attached {budget:.0f}s after the service exited:\n{output}"
        )
    drainer = _DRAINERS.get(watcher.pid)
    if drainer is not None:
        drainer.join(timeout=5)
    return _output_of(watcher)


# --------------------------------------------------------------------------- #
# The watch returns when the service exits
# --------------------------------------------------------------------------- #


def test_the_attached_log_stream_returns_when_the_service_exits(lib_runtime: Path, owned_service):
    """The core of the ticket: a bare `tail -f` never returns, and this one must."""
    service = owned_service()
    log_file = lib_runtime / ".boss_agent" / "service.log"
    log_file.write_text("service started\n", encoding="utf-8")

    watcher = _start_watch(lib_runtime, service.pid, log_file)

    # Still following a live service: it must not have detached early.
    with contextlib.suppress(subprocess.TimeoutExpired):
        watcher.wait(timeout=ATTACH_HOLD_SEC)
        pytest.fail("the watcher gave up on a service that was still running")
    assert watcher.poll() is None, "the watcher exited while its service was still running"

    service.kill()
    service.wait(timeout=10)

    output = _assert_returns(watcher)
    assert "service started" in output, f"the log stream itself was lost:\n{output}"


def test_the_stop_notice_names_the_service_and_the_pid(lib_runtime: Path, owned_service):
    """The operator must be told *which* service stopped, and at which PID.

    The service is stopped the way every runner stops it: the pidfile is cleared. That is
    the case that must stay a plain stop — the pidfile's mere presence says nothing, and
    only a *different live* PID in it means the service was restarted.
    """
    service = owned_service()
    pid_file = lib_runtime / ".boss_agent" / "service.pid"
    pid_file.write_text(f"{service.pid}\n", encoding="utf-8")
    log_file = lib_runtime / ".boss_agent" / "service.log"
    log_file.write_text("service started\n", encoding="utf-8")

    watcher = _start_watch(lib_runtime, service.pid, log_file, pid_file=pid_file)
    service.kill()
    service.wait(timeout=10)
    pid_file.unlink()  # `runner_pidfile_clear`, which every runner does on stop
    output = _assert_returns(watcher)

    expected = STOP_NOTICE.format(label=WORKER_LABEL, pid=service.pid)
    assert expected in output, (
        f"expected the stop notice {expected!r}; the watcher printed:\n{output}"
    )


def test_the_stop_notice_is_reported_even_though_the_service_wrote_nothing(
    lib_runtime: Path, owned_service
):
    """A daemon killed with SIGKILL never narrates its own death; the watcher must.

    The log file is the *file*, and a file does not know its writer died. Announcing the
    stop is the watcher's job precisely because the service cannot do it.
    """
    service = owned_service()
    log_file = lib_runtime / ".boss_agent" / "service.log"
    log_file.write_text("service started\n", encoding="utf-8")

    watcher = _start_watch(lib_runtime, service.pid, log_file)
    service.kill()
    service.wait(timeout=10)
    output = _assert_returns(watcher)

    assert "已停止" in output
    assert "service started" in output, "the last lines the service wrote must still be shown"


def test_the_watch_has_one_wording_for_every_service(lib_runtime: Path, owned_service):
    """The notice is a contract with the operator, so it is defined once, in the library.

    Same reasoning as the shutdown acknowledgment and the lifecycle events: five runners,
    one sentence, drifting apart the moment a runner is copied.
    """
    library = RUNNER_LIB.read_text(encoding="utf-8")
    assert "RUNNER_ATTACHED_STOP_EVENT=" in library, (
        "runner_lib.sh no longer owns the attached-stream stop notice; the runners reference it"
    )

    for label in ("Worker", "Web Dashboard"):
        service = owned_service()
        log_file = lib_runtime / ".boss_agent" / f"{label.replace(' ', '-')}.log"
        log_file.write_text("service started\n", encoding="utf-8")
        watcher = _start_watch(lib_runtime, service.pid, log_file, label=label)
        service.kill()
        service.wait(timeout=10)
        output = _assert_returns(watcher)
        assert STOP_NOTICE.format(label=label, pid=service.pid) in output


def test_a_restart_from_another_terminal_is_announced_as_a_stop_and_restart(
    lib_runtime: Path, owned_service
):
    """The old PID is gone and the pidfile now names a *different live* PID: say so.

    Issue #425 asks for a restart to be announced as a stop-and-restart, not silently
    followed — and silently *following* is what staying on the same file would mean, leaving
    the operator attached to a process they can no longer Ctrl+C and believing they are
    watching the current one.

    Telling the two apart takes evidence only the runner has. A log file cannot say whether
    its writer died or was replaced, and the port may have been handed to an unrelated
    process; the service's own pidfile naming a different live process is the one thing that
    substantiates "restarted". So the notice names the replacement PID — and without that
    evidence the same watcher still claims the plain stop.
    """
    pid_file = lib_runtime / ".boss_agent" / "service.pid"
    first = owned_service()
    pid_file.write_text(f"{first.pid}\n", encoding="utf-8")
    log_file = lib_runtime / ".boss_agent" / "service.log"
    log_file.write_text("first instance\n", encoding="utf-8")

    watcher = _start_watch(lib_runtime, first.pid, log_file, pid_file=pid_file)

    first.kill()
    first.wait(timeout=10)

    # The replacement daemon, started from "another terminal", now owning the same pidfile.
    replacement = owned_service()
    pid_file.write_text(f"{replacement.pid}\n", encoding="utf-8")
    log_file.write_text("second instance\n", encoding="utf-8")

    output = _assert_returns(watcher)
    expected = RESTART_NOTICE.format(label=WORKER_LABEL, pid=first.pid, new_pid=replacement.pid)
    assert expected in output, (
        f"a restart was announced as something other than a stop-and-restart:\n{output}"
    )
    # Compared as whole lines: the stop notice is a *prefix* of the restart notice, so a
    # substring test would call a correctly-reported restart a plain stop.
    notices = [line for line in output.splitlines() if line.startswith("🛑 [")]
    assert notices == [expected], (
        f"expected exactly the stop-and-restart notice and nothing else, got {notices!r}"
    )
    assert replacement.poll() is None, "the replacement daemon must be left alone"
    _terminate(watcher)


def test_a_stop_whose_pidfile_names_a_dead_pid_is_not_claimed_as_a_restart(
    lib_runtime: Path, owned_service
):
    """Only a *live* replacement proves a restart.

    A pidfile that still names the PID that just died — the state every runner leaves
    behind between the service exiting and the pidfile being cleared — is not evidence of
    anything. Claiming a restart from it would be the dishonesty #425 warns about, in the
    opposite direction.
    """
    service = owned_service()
    pid_file = lib_runtime / ".boss_agent" / "service.pid"
    pid_file.write_text(f"{service.pid}\n", encoding="utf-8")
    log_file = lib_runtime / ".boss_agent" / "service.log"
    log_file.write_text("first instance\n", encoding="utf-8")

    watcher = _start_watch(lib_runtime, service.pid, log_file, pid_file=pid_file)
    service.kill()
    service.wait(timeout=10)

    output = _assert_returns(watcher)
    assert STOP_NOTICE.format(label=WORKER_LABEL, pid=service.pid) in output
    assert "已停止并重启" not in output, (
        f"a restart was claimed from a pidfile naming a dead process:\n{output}"
    )


# --------------------------------------------------------------------------- #
# The safety half: watching a service must never signal it
# --------------------------------------------------------------------------- #


def test_detaching_with_ctrl_c_does_not_signal_the_service(lib_runtime: Path, owned_service):
    """Ctrl+C stops the log stream, never the background service.

    The single most important assertion in this file. `runner_attached_logs` has always
    trapped INT to exit cleanly, and a stop *is* a signal: the moment the watch grew the
    ability to observe a PID, it also grew the power to kill one, and the trap has to stay
    on the observe-only side of that line.
    """
    service = owned_service()
    log_file = lib_runtime / ".boss_agent" / "service.log"
    log_file.write_text("service started\n", encoding="utf-8")
    watcher = _start_watch(lib_runtime, service.pid, log_file)

    os.kill(watcher.pid, signal.SIGINT)
    output = _assert_returns(watcher)

    assert watcher.returncode == 0, f"detach should exit cleanly, got {watcher.returncode}"
    assert "Detached from" in output, f"detach printed no confirmation:\n{output}"
    assert service.poll() is None, "Ctrl+C on the attached log stream killed the background service"


def test_detaching_leaves_no_tail_child_behind(lib_runtime: Path, owned_service):
    """The `tail` child is the watcher's own; detach takes it down, nothing else."""
    service = owned_service()
    log_file = lib_runtime / ".boss_agent" / "service.log"
    log_file.write_text("service started\n", encoding="utf-8")
    watcher = _start_watch(lib_runtime, service.pid, log_file)

    tail_pid = _tail_child_of(watcher)
    os.kill(watcher.pid, signal.SIGINT)
    _assert_returns(watcher)

    assert _is_gone(tail_pid), f"the tail child {tail_pid} outlived the detached watcher"
    assert service.poll() is None, "detach signalled the service"


def _tail_child_of(watcher: subprocess.Popen) -> int:
    """The PID of the `tail` the watcher is following, once it has forked one."""
    deadline = time.monotonic() + WATCH_RETURN_BUDGET_SEC
    while time.monotonic() < deadline:
        found = subprocess.run(
            ["pgrep", "-P", str(watcher.pid)],
            capture_output=True,
            text=True,
        ).stdout.split()
        for pid in found:
            command = subprocess.run(
                ["ps", "-o", "command=", "-p", pid], capture_output=True, text=True
            ).stdout
            if "tail" in command:
                return int(pid)
        time.sleep(0.1)
    _terminate(watcher)
    raise AssertionError("the watcher never forked a tail child")


def _is_gone(pid: int) -> bool:
    """Whether a PID names no live, non-defunct process.

    The `Z` check mirrors `runner_process_alive`: a killed-but-unreaped child is not a
    survivor, and asserting otherwise would make this test fail on reaping timing alone.
    """
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    if state.returncode != 0:
        return True
    return state.stdout.strip().startswith("Z")


def test_a_service_that_is_already_dead_does_not_hang_the_watch(lib_runtime: Path):
    """A PID gone before the watch starts must return immediately, not spin forever."""
    log_file = lib_runtime / ".boss_agent" / "service.log"
    log_file.write_text("service started\n", encoding="utf-8")
    dead_pid = _unallocatable_pid()

    started_at = time.monotonic()
    watcher = _start_watch(lib_runtime, dead_pid, log_file)
    elapsed = time.monotonic() - started_at

    output = _assert_returns(watcher)
    assert elapsed < WATCH_RETURN_BUDGET_SEC
    assert STOP_NOTICE.format(label=WORKER_LABEL, pid=dead_pid) in output


def _unallocatable_pid() -> int:
    """A PID that cannot name a live process on the host running the test.

    PIDs are allocated from the kernel's ceiling downwards, so a value above `pid_max`
    cannot exist anywhere — no sleep, no race, no chance of colliding with a real service.
    """
    ceiling = 32768
    with contextlib.suppress(OSError, ValueError):
        ceiling = max(ceiling, int(Path("/proc/sys/kernel/pid_max").read_text().strip()))
    return ceiling + 1


def test_an_unresolvable_pid_keeps_streaming_and_announces_nothing(lib_runtime: Path):
    """`unknown` is not a death certificate: the watch degrades to the old `tail -f`.

    Three Dedicated Runner Scripts pass this literal when they can see their service is up
    — its health check answered — but could not resolve the PID owning it. `appium.sh`,
    `pocketbase.sh` and `dashboard.sh` all reach the attach path that way, so it is an
    anticipated route, not an edge case.

    `runner_process_alive` requires a number, so `ps -p unknown` fails and the watch read
    that as GONE: the terminal announced the stop of a service that was answering a moment
    earlier, then detached from a perfectly live stream. An unwatchable PID is *no
    information*, never a death — the watcher must keep following and must never print the
    notice. This is the old `tail -f` behaviour, restored for exactly the case the watch
    cannot substantiate anything about.
    """
    log_file = lib_runtime / ".boss_agent" / "service.log"
    log_file.write_text("service started\n", encoding="utf-8")

    watcher = _start_watch(lib_runtime, UNRESOLVED_PID, log_file)

    with contextlib.suppress(subprocess.TimeoutExpired):
        watcher.wait(timeout=ATTACH_HOLD_SEC)
        pytest.fail("the watcher announced a stop for a PID it cannot watch")
    assert watcher.poll() is None, "the watcher exited on a PID it cannot watch"

    # The stream itself is untouched: a line written after the watch started still arrives.
    log_file.write_text("service still running\n", encoding="utf-8")
    _wait_for_text(watcher, "service still running", budget=WATCH_RETURN_BUDGET_SEC)

    output = _terminate(watcher)
    assert "已停止" not in output, (
        f"the watcher announced a stop it could not substantiate:\n{output}"
    )
    assert "service still running" in output, f"the log stream itself was lost:\n{output}"


def test_a_defunct_service_is_announced_as_stopped(lib_runtime: Path, defunct_pid: int):
    """A zombie is not a running service: it must not keep a watch alive.

    A defunct PID still answers `kill -0` and still shows up in `ps`, so an observer that
    only asked "does this PID exist?" would follow a corpse indefinitely.
    """
    log_file = lib_runtime / ".boss_agent" / "service.log"
    log_file.write_text("service started\n", encoding="utf-8")

    watcher = _start_watch(lib_runtime, defunct_pid, log_file)
    output = _assert_returns(watcher)

    assert STOP_NOTICE.format(label=WORKER_LABEL, pid=defunct_pid) in output


def test_the_watch_ends_when_the_log_stream_itself_ends(lib_runtime: Path, owned_service):
    """A `tail` that dies on its own must not leave the loop spinning on a dead child."""
    service = owned_service()
    log_file = lib_runtime / ".boss_agent" / "service.log"
    log_file.write_text("service started\n", encoding="utf-8")
    watcher = _start_watch(lib_runtime, service.pid, log_file)

    os.kill(_tail_child_of(watcher), signal.SIGKILL)
    output = _assert_returns(watcher)

    assert STOP_NOTICE.format(label=WORKER_LABEL, pid=service.pid) not in output, (
        "the service never stopped; the watcher must not announce a stop it did not observe"
    )
    assert service.poll() is None, "the watcher's own tail died; the service must be untouched"


# --------------------------------------------------------------------------- #
# The Dedicated Runner Script paths that attach to a service
# --------------------------------------------------------------------------- #


@pytest.fixture
def worker_runtime(tmp_path: Path):
    """A `worker.sh` root whose pre-flight gates pass and whose daemon is a local stub.

    `worker.sh` refuses to start without a reachable State Stream Broker, an AVD that
    reports ready, and a reachable Appium server. All three are satisfied by local
    stand-ins: the two HTTP dependencies run on their own ephemeral ports, and the AVD is a
    stub script that exits 0, because waking a real Virtual Device Session is precisely what
    this suite must never do.

    Yields `(root, broker_port, appium_port)`.
    """
    root = tmp_path / "repo"
    (root / ".boss_agent").mkdir(parents=True)
    shutil.copy2(WORKER_SH, root / "worker.sh")
    shutil.copy2(RUNNER_LIB, root / "runner_lib.sh")
    (root / "worker.sh").chmod(0o755)
    (root / "emulator.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (root / "emulator.sh").chmod(0o755)
    (root / "scripts").mkdir()
    (root / "scripts" / "worker.py").write_text(_DAEMON_STUB, encoding="utf-8")

    # `worker.sh` has two pre-flight gates: the State Stream Broker health probe and the
    # Appium server reachability probe. Both are satisfied by `_ALWAYS_OK_SERVER`, and both
    # must be answered by *this* suite on its own ephemeral port.
    #
    # Leaving APPIUM_URL unset made these tests silently depend on whatever happens to be
    # listening on the real 4723 — a shared, machine-wide service that any other worktree can
    # stop at any moment. They then failed for reasons that had nothing to do with the liveness
    # watch under test, and passed or failed depending on another checkout's schedule. See
    # docs/agents/testing.md: an E2E test allocates its own ephemeral port and never collides
    # with services belonging to other worktrees.
    broker = _start_always_ok_server()
    appium = _start_always_ok_server()
    try:
        yield root, broker.port, appium.port
    finally:
        for server in (broker, appium):
            server.terminate()


def _start_always_ok_server() -> _StubServer:
    """Run `_ALWAYS_OK_SERVER` on its own free port and return a handle that owns it."""
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "-c", _ALWAYS_OK_SERVER, str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    wait_for_port_bound(port)
    return _StubServer(port, process)


#: An HTTP server answering 200 to every path — the shape both pre-flight gates probe.
_ALWAYS_OK_SERVER = """
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
"""


@dataclass
class _StubServer:
    """An `_ALWAYS_OK_SERVER` process and the ephemeral port it was given."""

    port: int
    process: subprocess.Popen

    def terminate(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=10)


def _run_worker(
    runtime_root: Path, broker_port: int, appium_port: int, *args: str
) -> subprocess.Popen:
    worker = subprocess.Popen(
        [_bash(), "worker.sh", *args],
        cwd=str(runtime_root),
        env={
            **os.environ,
            "PATH": f"{Path(sys.executable).parent}:{os.environ.get('PATH', '')}",
            "POCKETBASE_URL": f"http://127.0.0.1:{broker_port}",
            "APPIUM_URL": f"http://127.0.0.1:{appium_port}",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    _drain_output(worker)
    return worker


def _launch_foreground_worker(
    runtime_root: Path, broker_port: int, appium_port: int
) -> subprocess.Popen:
    """`./worker.sh` with no arguments: launch the Automation Worker and follow its log.

    This is the path bare `./run.sh` lands on, and it is the one the ticket calls out by
    name — it used to end in a bare `tail -n 0 -f` with no PID watch at all.
    """
    worker = _run_worker(runtime_root, broker_port, appium_port)
    pid_file = runtime_root / ".boss_agent" / "worker.pid"
    deadline = time.monotonic() + WATCH_RETURN_BUDGET_SEC
    while time.monotonic() < deadline:
        if pid_file.exists() and pid_file.read_text(encoding="utf-8").strip().isdigit():
            return worker
        if worker.poll() is not None:
            pytest.fail(f"the foreground worker exited early:\n{_output_of(worker)}")
        time.sleep(0.05)
    _terminate(worker)
    pytest.fail("the foreground worker never recorded a daemon PID")


def _stop_the_launched_daemon(runtime_root: Path) -> int:
    """Kill the launched Automation Worker the way another terminal's `./worker.sh stop` would."""
    pid_file = runtime_root / ".boss_agent" / "worker.pid"
    pid = int(pid_file.read_text(encoding="utf-8").strip())
    os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + WATCH_RETURN_BUDGET_SEC
    while time.monotonic() < deadline and not _is_gone(pid):
        time.sleep(0.05)
    assert _is_gone(pid), f"the launched daemon {pid} survived SIGKILL"
    return pid


def test_the_foreground_start_mode_watches_the_daemon_it_launched(worker_runtime):
    """`./worker.sh` in the foreground must unblock when its daemon is killed elsewhere."""
    runtime_root, broker_port, appium_port = worker_runtime
    worker = _launch_foreground_worker(runtime_root, broker_port, appium_port)

    with contextlib.suppress(subprocess.TimeoutExpired):
        worker.wait(timeout=ATTACH_HOLD_SEC)
        pytest.fail("the foreground worker gave up on a daemon that was still running")

    pid = _stop_the_launched_daemon(runtime_root)
    output = _assert_returns(worker)

    assert STOP_NOTICE.format(label=WORKER_LABEL, pid=pid) in output, (
        f"the foreground start path announced no stop; it printed:\n{output}"
    )


def test_the_worker_attach_route_watches_the_daemon(worker_runtime):
    """`./worker.sh attach` is what `run.sh` execs for its bare and restart routes."""
    runtime_root, broker_port, appium_port = worker_runtime
    launcher = _launch_daemon_only(runtime_root, broker_port, appium_port)
    daemon_pid = int((runtime_root / ".boss_agent" / "worker.pid").read_text().strip())

    attach = _run_worker(runtime_root, broker_port, appium_port, "attach")
    _drain_output(attach)
    _wait_for_text(attach, "Attaching to live log stream", budget=WATCH_RETURN_BUDGET_SEC)

    # The daemon, not the launcher that spawned it: `--daemon` backgrounded the real worker.
    os.kill(daemon_pid, signal.SIGKILL)
    output = _assert_returns(attach)

    assert STOP_NOTICE.format(label=WORKER_LABEL, pid=daemon_pid) in output
    _terminate(launcher)


def _launch_daemon_only(runtime_root: Path, broker_port: int, appium_port: int) -> subprocess.Popen:
    """Start the Automation Worker in the background — no log stream of its own."""
    started = _run_worker(runtime_root, broker_port, appium_port, "start", "--daemon")
    pid_file = runtime_root / ".boss_agent" / "worker.pid"
    deadline = time.monotonic() + WATCH_RETURN_BUDGET_SEC
    while time.monotonic() < deadline:
        if pid_file.exists() and pid_file.read_text(encoding="utf-8").strip().isdigit():
            return started
        if started.poll() is not None:
            pytest.fail(f"the daemon failed to start:\n{_output_of(started)}")
        time.sleep(0.05)
    _terminate(started)
    pytest.fail("the background daemon never recorded a PID")


def test_the_orchestrator_reaches_the_watch_through_the_worker_attach_route(
    worker_runtime, tmp_path: Path
):
    """`run.sh`'s bare and restart routes attach by exec'ing `./worker.sh attach`.

    Asserted on the orchestrator's own source rather than by running it: those routes
    `exec` into the Automation Worker daemon, which would mean booting an emulator to
    observe a two-line dispatch decision. What must be pinned is that the routes still
    reach the watch, and that `--daemon` still opts out.
    """
    orchestrator = RUN_SH.read_text(encoding="utf-8")
    assert "exec ./worker.sh attach" in orchestrator, (
        "run.sh no longer reaches the attached log stream through ./worker.sh attach"
    )
    assert worker_runtime[0].name == "repo", "the runtime root fixture must stay throwaway"

    worker = WORKER_SH.read_text(encoding="utf-8")
    assert "runner_attached_logs" in worker, "the worker no longer watches its attached logs"
    assert "runner_watch_log_stream" in worker, (
        "the worker's foreground start path no longer watches the daemon it launched"
    )
    # The comment on that path explains the bare-tail history; only *code* may not do it.
    assert 'tail -n 0 -f "${WORKER_LOG_FILE}"' not in _code_only(worker), (
        "worker.sh still ends its foreground start path in a bare `tail -n 0 -f`"
    )


def _code_only(script: str) -> str:
    """The script with its `#` comment lines stripped, so prose cannot satisfy a code assertion."""
    return "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
