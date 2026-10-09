"""
tests/e2e/test_runner_lifecycle_events.py
==========================================
Every Dedicated Runner Script records the Graceful Shutdown Protocol into its own log.

`dashboard.sh` was the one script that did: it wrote an acknowledgment, an escalation line
if it had to SIGKILL, and a completion line to `.boss_agent/web.log`. The other four printed
their stop narrative to the operator's terminal and left their own log files untouched — so
the E2E Pre-Test Teardown Gate, which greps those files, could never see what happened, and
an operator reading `worker.log` after the fact saw a daemon that stopped for no stated
reason.

These tests drive the real scripts against throwaway runtime roots and real processes. They
live in the E2E tier because every one of them spawns a process to stop; the fast unit tier
rejects that outright.

Two properties are asserted together, and the second is the one that is easy to lose: an
*idle* stop must still write nothing. Lifecycle events are the record of a real stop, not a
heartbeat — a runner that logged unconditionally would create a log file on every
`./run.sh stop` and fill it with phantom stops, and the dashboard's cheap-no-op test already
pins that property for `dashboard.sh`.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from _service_harness import REPO_ROOT, free_port, held_free_port, wait_for_port_bound

pytestmark = pytest.mark.e2e

RUNNER_LIB = REPO_ROOT / "runner_lib.sh"
STOP_BUDGET_SEC = 20.0
#: How long a test lets a Virtual Device Session restart spend on the boot wait it cannot
#: satisfy hermetically, before taking the runner down. Long enough for the stop half.
BOOT_WAIT_CEILING_SEC = 8.0

# The wording the shared seam emits. Restated here on purpose: a test states the contract it
# expects, while a runner only references the seam (see
# test_the_lifecycle_events_have_one_definition_per_service).
STOP_REQUESTED = "Stop requested"
STOP_COMPLETED = "Stop completed"
RESTART_REQUESTED = "Restart requested"
RESTART_COMPLETED = "Restart completed"

#: A daemon stub that writes its readiness line WITHOUT flushing it. Whether that line
#: reaches the log while the process is still running is exactly the block-buffering
#: question `PYTHONUNBUFFERED=1` answers, so the stub must not paper over it by flushing.
_DAEMON_STUB = """
import sys
import time

sys.stdout.write("UNFLUSHED_DAEMON_MARKER\\n")
time.sleep(120)
"""

#: A fake Android SDK: the runner resolves `adb` and `emulator` through
#: `runner_find_binary`, which checks `$ANDROID_HOME` candidates *before* PATH, so pointing
#: ANDROID_HOME at this directory is what stops a test from ever reaching the real binaries
#: on the machine. The stub `emulator` refuses to boot even if something launches it.
_STUB_EMULATOR = """#!/usr/bin/env bash
echo "stub emulator: refusing to boot a device from a test" >&2
exit 1
"""

_FAKE_ADB = """#!/usr/bin/env bash
# Reports the device the test rigged up; no device is ever contacted.
case "$*" in
    *devices*) printf 'List of devices attached\\nemulator-5554\\tdevice\\n' ;;
    *"emu avd name"*) printf '%s\\n' "{avd}" ;;
    *) exit 0 ;;
esac
"""

_IDLE_ADB = """#!/usr/bin/env bash
# No devices attached: every transport the runner probes stays empty.
case "$*" in
    *devices*) printf 'List of devices attached\\n' ;;
    *) exit 0 ;;
esac
"""

#: A name no real AVD can have. `emulator.sh` resolves its process sweep from the AVD name,
#: so an ordinary name here would let the sweep match — and SIGTERM — the Virtual Device
#: Session another worktree legitimately left running.
PROBE_AVD = "runner_lifecycle_probe_avd"


def _bash() -> str:
    return shutil.which("bash") or "/bin/bash"


def _runner_root(tmp_path: Path, script: str) -> Path:
    """A throwaway script root: the real runner, the real library, a private `.boss_agent`."""
    root = tmp_path / "repo"
    (root / ".boss_agent").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / script, root / script)
    shutil.copy2(RUNNER_LIB, root / "runner_lib.sh")
    (root / script).chmod(0o755)
    return root


def _run(runtime_root: Path, script: str, *args: str, **env: str) -> subprocess.CompletedProcess:
    """Run one runner command inside its throwaway root."""
    return subprocess.run(
        [_bash(), script, *args],
        cwd=str(runtime_root),
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=STOP_BUDGET_SEC,
    )


def _log_of(runtime_root: Path, name: str) -> Path:
    """The runtime log of a runner, wherever the Infrastructure/app split put it.

    `pocketbase.sh` anchors at the git common root, which for a throwaway root that is not a
    repository degrades to the root itself; searching for the file keeps the test honest
    about whichever location the policy chose instead of hard-coding one.
    """
    direct = runtime_root / ".boss_agent" / name
    if direct.exists():
        return direct
    found = sorted(runtime_root.rglob(name))
    assert found, f"{name} was not written anywhere under {runtime_root}"
    return found[0]


def _read(log_file: Path) -> str:
    return log_file.read_text(encoding="utf-8", errors="replace")


def _sleep_process(argv0: str = "runner-fixture") -> subprocess.Popen:
    """A live process the runner can legitimately claim as its own service."""
    return subprocess.Popen(
        [sys.executable, "-c", f'import os; os.execv("/bin/sleep", [{argv0!r}, "120"])']
    )


@contextlib.contextmanager
def _owned_process(runtime_root: Path, pid_name: str):
    """A live process recorded in a runner's pidfile, reaped if the test fails first."""
    process = _sleep_process()
    try:
        pid_file = runtime_root / ".boss_agent" / pid_name
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(process.pid), encoding="utf-8")
        yield process
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


#: An HTTP server that answers 200 to every path — the Shape the PocketBase and Appium
#: pre-flight health checks probe for.
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


@contextlib.contextmanager
def _ok_http_server():
    """A real HTTP server, in a process of its own, answering 200 to everything.

    A separate process rather than a thread on purpose: the runner under test resolves the
    owner of a port it has been asked to reclaim and signals that PID, and a server listening
    on a thread of the test session *is* the test session — so a threaded stub turns a
    routine port reclaim into a SIGTERM of the whole pytest run.
    """
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "-c", _ALWAYS_OK_SERVER, str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        wait_for_port_bound(port)
        yield port
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture
def worker_daemon_runtime(tmp_path: Path):
    """A worker root whose pre-flight gates pass and whose daemon is a local stub.

    `worker.sh` refuses to start without a reachable State Stream Broker, an AVD that
    reports ready, and a reachable Appium. Only the first and third are real services a
    test may stand up cheaply; the AVD is a stub script that exits 0, because waking a real
    Virtual Device Session is precisely what this suite must never do.
    """
    runtime_root = _runner_root(tmp_path, "worker.sh")
    fake_emulator = runtime_root / "emulator.sh"
    fake_emulator.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_emulator.chmod(0o755)
    scripts_dir = runtime_root / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "worker.py").write_text(_DAEMON_STUB, encoding="utf-8")
    with _ok_http_server() as port:
        try:
            yield runtime_root, port
        finally:
            _run(
                runtime_root,
                "worker.sh",
                "stop",
                POCKETBASE_URL=f"http://127.0.0.1:{port}",
            )


# --------------------------------------------------------------------------- #
# stop leaves a stop-confirmation and a completion event in the service's own log
# --------------------------------------------------------------------------- #


def test_worker_stop_records_the_lifecycle_events(tmp_path: Path):
    runtime_root = _runner_root(tmp_path, "worker.sh")
    with _owned_process(runtime_root, "worker.pid") as process:
        result = _run(runtime_root, "worker.sh", "stop")
        assert result.returncode == 0, result.stderr
        assert process.poll() is not None, "the Automation Worker daemon survived stop"

    recorded = _read(_log_of(runtime_root, "worker.log"))
    assert STOP_REQUESTED in recorded, f"stop left no confirmation in worker.log:\n{recorded}"
    assert STOP_COMPLETED in recorded, f"stop left no completion event in worker.log:\n{recorded}"
    assert recorded.index(STOP_REQUESTED) < recorded.index(STOP_COMPLETED), (
        "the completion event must follow the confirmation, not precede it"
    )


def test_pocketbase_stop_records_the_lifecycle_events(tmp_path: Path):
    runtime_root = _runner_root(tmp_path, "pocketbase.sh")
    port = free_port()
    with _owned_process(runtime_root, "pocketbase.pid") as process:
        result = _run(runtime_root, "pocketbase.sh", "stop", PB_HTTP=f"127.0.0.1:{port}")
        assert result.returncode == 0, result.stderr
        assert process.poll() is not None, "the PocketBase process survived stop"

    recorded = _read(_log_of(runtime_root, "pocketbase.log"))
    assert STOP_REQUESTED in recorded, f"stop left no confirmation in pocketbase.log:\n{recorded}"
    assert STOP_COMPLETED in recorded, f"stop left no completion event:\n{recorded}"


def test_appium_stop_records_the_lifecycle_events(tmp_path: Path):
    runtime_root = _runner_root(tmp_path, "appium.sh")
    with _owned_process(runtime_root, "appium.pid") as process:
        result = _run(runtime_root, "appium.sh", "stop", APPIUM_PORT=str(free_port()))
        assert result.returncode == 0, result.stderr
        assert process.poll() is not None, "the Appium process survived stop"

    recorded = _read(_log_of(runtime_root, "appium.log"))
    assert STOP_REQUESTED in recorded, f"stop left no confirmation in appium.log:\n{recorded}"
    assert STOP_COMPLETED in recorded, f"stop left no completion event:\n{recorded}"


def _fake_sdk(tmp_path: Path, adb_script: str) -> Path:
    """A fake Android SDK carrying a scripted `adb` and an `emulator` that refuses to boot."""
    sdk = tmp_path / "fake-android-sdk"
    for relative, script in (
        ("platform-tools/adb", adb_script),
        ("emulator/emulator", _STUB_EMULATOR),
    ):
        path = sdk / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)
    return sdk


def emulator_environment(sdk: Path, **env: str) -> dict[str, str]:
    """Environment in which the runner can only reach the stub SDK.

    `REMOTE_ADB_PORT` is pinned to a free port, and that is load-bearing rather than
    cosmetic. The runner's default is 6555 — the *developer's real* bridge port — and it
    resolves the port's owner by looking at live processes. A test that left it on the
    default would point `stop` at whatever is really listening there, and an "idle stop"
    asserting no lifecycle event was recorded would instead record a perfect one against the
    developer's own bridge, and signal it. The same reason `_runner_harness` renames the AVD.
    """
    base = {k: v for k, v in os.environ.items() if k != "ANDROID_SDK_ROOT"}
    base["ANDROID_HOME"] = str(sdk)
    base["PATH"] = f"{sdk / 'platform-tools'}:{base.get('PATH', '')}"
    base["REMOTE_ADB_PORT"] = str(free_port())
    base["TARGET_ADB_PORT"] = "5555"
    base.update(env)
    return base


def _emulator_root(tmp_path: Path) -> Path:
    return _runner_root(tmp_path, "emulator.sh")


@contextlib.contextmanager
def _fake_bridge_process(runtime_root: Path):
    """A live process recorded in `remote_bridge.pid`, wearing the bridge's argv.

    The AVD stopped being what `stop` releases, so the Remote ADB Bridge is the service whose
    shutdown these lifecycle events now record. The argv has to carry `remote_adb_bridge`
    because `get_running_bridge_pid` refuses a pidfile naming anything else — a fixture that
    did not look like a bridge would be ignored, and the test would pass by observing nothing.
    """
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            'import os; os.execv("/bin/sleep", ["remote_adb_bridge --port 6555", "120"])',
        ]
    )
    try:
        time.sleep(0.3)  # let the exec land, so the argv is already the bridge's spelling
        pid_file = runtime_root / ".boss_agent" / "remote_bridge.pid"
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(process.pid), encoding="utf-8")
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def _fake_emulator_process(avd: str = PROBE_AVD) -> subprocess.Popen:
    """A host process whose argv is the one `emulator.sh` recognises as the AVD's own.

    Its purpose inverted when the AVD stopped being stoppable: it used to be a process for
    the runner to reap, and is now one that must *survive* every command in the runner. So a
    test that starts one is asserting that nothing signalled it, which is only meaningful
    because the argv is a spelling `emulator.sh` really scans for — otherwise the fixture
    would pass by never being visible to the scan.
    """
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f'import os; os.execv("/bin/sleep", ["emulator @{avd} -no-snapshot-load", "120"])',
        ]
    )
    time.sleep(0.3)  # let the exec land, so the argv is already the emulator's spelling
    return process


def test_emulator_stop_records_the_lifecycle_events(tmp_path: Path):
    runtime_root = _emulator_root(tmp_path)
    sdk = _fake_sdk(tmp_path, _FAKE_ADB.format(avd=PROBE_AVD))
    process = _fake_emulator_process()
    try:
        (runtime_root / ".boss_agent" / "emulator.pid").write_text(
            str(process.pid), encoding="utf-8"
        )
        with _fake_bridge_process(runtime_root) as bridge:
            result = subprocess.run(
                [_bash(), "emulator.sh", "--avd", PROBE_AVD, "stop"],
                cwd=str(runtime_root),
                env=emulator_environment(sdk),
                capture_output=True,
                text=True,
                timeout=STOP_BUDGET_SEC,
            )
            assert bridge.poll() is not None, "the Remote ADB Bridge survived the stop"
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        assert process.poll() is None, (
            "the AVD process was signalled; no command in this runner stops it"
        )
        assert "emu kill" not in result.stdout, (
            f"`stop` reached for the emulator console:\n{result.stdout}"
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    recorded = _read(_log_of(runtime_root, "emulator.log"))
    assert STOP_REQUESTED in recorded, f"stop left no confirmation in emulator.log:\n{recorded}"
    assert STOP_COMPLETED in recorded, f"stop left no completion event:\n{recorded}"
    # What `stop` actually releases is the Remote ADB Bridge, and the recorded handle is the
    # process it signalled — never an invented PID, and never the AVD.
    assert "emulator-5554" not in recorded, (
        f"the stop was attributed to the AVD, which nothing here stops:\n{recorded}"
    )


def test_emulator_stop_records_nothing_when_no_device_is_running(tmp_path: Path):
    """The same cheap-no-op an idle dashboard stop has, on the Infrastructure side."""
    runtime_root = _emulator_root(tmp_path)
    sdk = _fake_sdk(tmp_path, _IDLE_ADB)
    result = subprocess.run(
        [_bash(), "emulator.sh", "--avd", PROBE_AVD, "stop"],
        cwd=str(runtime_root),
        env=emulator_environment(sdk),
        capture_output=True,
        text=True,
        timeout=STOP_BUDGET_SEC,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert not (runtime_root / ".boss_agent" / "emulator.log").exists(), (
        "an idle stop fabricated a lifecycle event; events record real stops, not heartbeats"
    )


# --------------------------------------------------------------------------- #
# restart records both the stop and the restart
# --------------------------------------------------------------------------- #


def test_worker_restart_records_the_stop_and_the_restart(tmp_path: Path):
    runtime_root = _runner_root(tmp_path, "worker.sh")
    with _owned_process(runtime_root, "worker.pid") as process:
        # The start half cannot succeed without a State Stream Broker, and the stop half is
        # exactly what this asserts; the unreachable broker is what keeps the test hermetic.
        _run(
            runtime_root,
            "worker.sh",
            "restart",
            POCKETBASE_URL="http://127.0.0.1:59999",
        )
        assert process.poll() is not None, "the Automation Worker daemon survived restart"

    recorded = _read(_log_of(runtime_root, "worker.log"))
    for event in (RESTART_REQUESTED, STOP_REQUESTED, STOP_COMPLETED):
        assert event in recorded, f"restart did not record {event!r} in worker.log:\n{recorded}"
    assert recorded.index(RESTART_REQUESTED) < recorded.index(STOP_REQUESTED), (
        "the restart is recorded before the stop it performs"
    )


def test_pocketbase_restart_records_the_stop_and_the_restart(tmp_path: Path):
    """The stop and the restart are both in the log; the start half needs a real broker.

    The completion marker is not asserted here: reaching it means a live State Stream Broker
    is answering, which is machine-shared infrastructure a test may not stand up. The data
    directory is pre-seeded so the start half does not even run the real `pocketbase` binary
    on its way to failing — it aborts at the schema provisioner, which has no script in a
    throwaway root.
    """
    runtime_root = _runner_root(tmp_path, "pocketbase.sh")
    data_dir = runtime_root / "pb-data"
    data_dir.mkdir()
    (data_dir / "data.db").write_bytes(b"")  # skip the real binary's `migrate up`
    port = free_port()
    with _owned_process(runtime_root, "pocketbase.pid") as process:
        result = _run(
            runtime_root,
            "pocketbase.sh",
            "restart",
            PB_HTTP=f"127.0.0.1:{port}",
            PB_DATA_DIR=str(data_dir),
        )
        assert process.poll() is not None, "the PocketBase process survived restart"

    recorded = _read(_log_of(runtime_root, "pocketbase.log"))
    for event in (RESTART_REQUESTED, STOP_REQUESTED, STOP_COMPLETED):
        assert event in recorded, f"restart did not record {event!r}:\n{recorded}"
    assert result.returncode != 0, "the start half was expected to abort without a broker"


def test_a_restart_whose_start_failed_is_not_recorded_as_complete(tmp_path: Path):
    """`Restart completed` means the service came back. A start that aborted must not claim it.

    `cmd_restart` runs `cmd_start` in a subshell so that an `exit` inside `cmd_start`'s own
    health check cannot take the confirmation down with it — and that same subshell threw
    the start's exit status away. A restart whose start failed still wrote "back online" into
    the very log the E2E Pre-Test Teardown Gate reads, which is how a service that is down
    gets recorded as up.

    The runner now captures that status and returns 1 itself. Asserting exactly 1 (rather
    than merely non-zero) is what makes this a test of the fix and not of `set -e`: left
    alone, the failed subshell aborted the runner from *inside* `cmd_restart`, so the exit
    status was whatever `set -e` happened to produce rather than a decision the runner made.

    The start half aborts where it always has in a throwaway root: the data directory is
    pre-seeded so the real `pocketbase` binary is never run, and the schema provisioner has
    no script to execute.
    """
    runtime_root = _runner_root(tmp_path, "pocketbase.sh")
    data_dir = runtime_root / "pb-data"
    data_dir.mkdir()
    (data_dir / "data.db").write_bytes(b"")  # skip the real binary's `migrate up`
    port = free_port()
    with _owned_process(runtime_root, "pocketbase.pid") as process:
        result = _run(
            runtime_root,
            "pocketbase.sh",
            "restart",
            PB_HTTP=f"127.0.0.1:{port}",
            PB_DATA_DIR=str(data_dir),
        )
        assert result.returncode == 1, (
            "a restart whose start failed must report the failure itself (1), not abort "
            f"from inside set -e ({result.returncode})"
        )
        assert process.poll() is not None, "the PocketBase process survived restart"

    recorded = _read(_log_of(runtime_root, "pocketbase.log"))
    assert RESTART_REQUESTED in recorded, f"the restart itself was never recorded:\n{recorded}"
    assert RESTART_COMPLETED not in recorded, (
        f"a start that never came up was recorded as a completed restart:\n{recorded}"
    )


#: Stands in for the Appium binary: binds the port it is handed and answers its status check.
_APPIUM_STUB_SOURCE = """
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 0


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


def _stub_home(tmp_path: Path) -> Path:
    """A HOME whose `.volta/bin/appium` the runner will find first.

    `runner_find_binary` takes the first executable candidate, and `$HOME/.volta/bin/appium`
    is the first one `appium.sh` offers — before the system-wide installs. Shadowing it here
    is what lets the start half be exercised without starting the Appium install the
    developer's machine happens to have.
    """
    stub = tmp_path / "appium_stub.py"
    stub.write_text(_APPIUM_STUB_SOURCE, encoding="utf-8")
    home = tmp_path / "stub-home"
    binary = home / ".volta" / "bin" / "appium"
    binary.parent.mkdir(parents=True)
    binary.write_text(
        f"#!{sys.executable}\nimport runpy\nrunpy.run_path({str(stub)!r}, run_name='__main__')\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return home


def test_appium_restart_records_the_stop_and_the_restart(tmp_path: Path):
    """A restart all the way through, against a stubbed Appium binary.

    The stub binds the port it is given and answers the status check, so the start half
    genuinely comes back up and the completion marker is reachable — without the test ever
    starting the Appium install the developer's machine happens to have.
    """
    runtime_root = _runner_root(tmp_path, "appium.sh")
    home = _stub_home(tmp_path)
    port = free_port()
    with _owned_process(runtime_root, "appium.pid") as process:
        result = _run(
            runtime_root,
            "appium.sh",
            "restart",
            "--daemon",
            APPIUM_PORT=str(port),
            HOME=str(home),
        )
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        assert process.poll() is not None, "the Appium process survived restart"
        # The stub is the only thing left holding the port; reap it by the runner's own route.
        _run(runtime_root, "appium.sh", "stop", APPIUM_PORT=str(port), HOME=str(home))

    recorded = _read(_log_of(runtime_root, "appium.log"))
    for event in (RESTART_REQUESTED, STOP_REQUESTED, STOP_COMPLETED, RESTART_COMPLETED):
        assert event in recorded, f"restart did not record {event!r}:\n{recorded}"


def test_emulator_restart_records_the_stop_and_the_restart(tmp_path: Path):
    """The restart half of a Virtual Device Session restart, bounded to what a test owns.

    The start half waits up to 90s for a real boot to complete, and there is no hermetic way
    to satisfy it — the only thing that boots an AVD is an AVD. So the runner is given a
    bounded window and killed once the stop half is done: what this contract is about, the
    stop and the restart being recorded, is settled long before it starts waiting. The
    completion marker is deliberately *not* asserted here, because reaching it would mean
    booting the machine's shared Virtual Device Session.
    """
    runtime_root = _emulator_root(tmp_path)
    sdk = _fake_sdk(tmp_path, _FAKE_ADB.format(avd=PROBE_AVD))
    process = _fake_emulator_process()
    (runtime_root / ".boss_agent" / "emulator.pid").write_text(str(process.pid), encoding="utf-8")
    _BRIDGE_RESTART_CTX = _fake_bridge_process(runtime_root)
    bridge = _BRIDGE_RESTART_CTX.__enter__()
    runner = subprocess.Popen(
        [_bash(), "emulator.sh", "--avd", PROBE_AVD, "restart", "--daemon"],
        cwd=str(runtime_root),
        env=emulator_environment(sdk),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        with contextlib.suppress(subprocess.TimeoutExpired):
            runner.wait(timeout=BOOT_WAIT_CEILING_SEC)
        assert process.poll() is None, "the AVD process was signalled; `restart` must not stop it"
        assert bridge.poll() is not None, "the Remote ADB Bridge survived the restart"
    finally:
        _kill_process_group(runner)
        _BRIDGE_RESTART_CTX.__exit__(None, None, None)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    recorded = _read(_log_of(runtime_root, "emulator.log"))
    for event in (RESTART_REQUESTED, STOP_REQUESTED, STOP_COMPLETED):
        assert event in recorded, f"restart did not record {event!r}:\n{recorded}"
    assert recorded.index(RESTART_REQUESTED) < recorded.index(STOP_REQUESTED), (
        "the restart is recorded before the stop it performs"
    )


@pytest.mark.parametrize(
    ("script", "pid_name", "log_name", "env"),
    [
        ("worker.sh", "worker.pid", "worker.log", {}),
        ("pocketbase.sh", "pocketbase.pid", "pocketbase.log", {"PB_HTTP": "127.0.0.1:{port}"}),
        ("appium.sh", "appium.pid", "appium.log", {"APPIUM_PORT": "{port}"}),
    ],
)
def test_an_idle_stop_records_nothing(tmp_path: Path, script, pid_name, log_name, env):
    """Stopping nothing writes nothing, for every runner that now has a lifecycle seam.

    A stop that logs unconditionally would create the service's log on every idle
    `./run.sh stop`, and the E2E Pre-Test Teardown Gate depends on an idle stop being a
    cheap no-op rather than a growing record of stops that never happened.

    The port is *held* for the duration rather than merely reserved and released, which is
    what makes "nothing is running" a fact instead of a hope. A released ephemeral port can
    be won by any concurrent process between here and the runner's own probe — and a runner
    resolving "who owns my port" would then adopt that stranger and log a perfectly correct
    stop, failing this test for a product that did nothing wrong.
    """
    runtime_root = _runner_root(tmp_path, script)
    with held_free_port() as port:
        overrides = {key: value.format(port=port) for key, value in env.items()}
        result = _run(runtime_root, script, "stop", **overrides)
    assert result.returncode == 0, result.stderr
    assert "No running" in result.stdout
    assert not (runtime_root / ".boss_agent" / log_name).exists(), (
        f"{script} stop fabricated a lifecycle event; events record real stops, not heartbeats"
    )


# --------------------------------------------------------------------------- #
# One log stream, one label
# --------------------------------------------------------------------------- #


def test_the_automation_worker_log_carries_one_label(tmp_path: Path):
    """`worker.log` names the Automation Worker, or nothing at all.

    `worker.sh` labelled its lifecycle events "Worker" and its escalations "Automation
    Worker" — two names for one service inside one file, which is exactly the synonym
    drift `GLOSSARY.md` exists to prevent. The domain term is **Automation Worker**, so
    every line a runner writes into `worker.log` carries that one label.

    The `[Worker]` prefix that survives in this repo belongs to the Python daemon's own
    shutdown marker (`boss_agent.worker.daemon.SHUTDOWN_ACK_MARKER`, which the teardown
    service greps). These shell lines do not produce it, and that marker is not touched
    here — so this asserts on `worker.log` as the runner alone wrote it.
    """
    runtime_root = _runner_root(tmp_path, "worker.sh")
    with _owned_process(runtime_root, "worker.pid") as process:
        result = _run(runtime_root, "worker.sh", "stop")
        assert result.returncode == 0, result.stderr
        assert process.poll() is not None, "the Automation Worker daemon survived stop"

    recorded = _read(_log_of(runtime_root, "worker.log"))
    assert STOP_REQUESTED in recorded, f"stop left no confirmation in worker.log:\n{recorded}"

    labels = set(re.findall(r"[🛑✅🔄⚠️] \[([^\]]+)\]", recorded))
    assert labels == {"Automation Worker"}, (
        f"worker.log named the Automation Worker by more than one name: {sorted(labels)}.\n"
        f"{recorded}"
    )


# --------------------------------------------------------------------------- #
# The daemon's own acknowledgment must reach the disk when it is logged
# --------------------------------------------------------------------------- #


def _wait_for(log_file: Path, marker: str, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log_file.exists() and marker in _read(log_file):
            return _read(log_file)
        time.sleep(0.05)
    return _read(log_file) if log_file.exists() else ""


def test_the_worker_daemons_acknowledgement_is_not_held_in_a_block_buffer(worker_daemon_runtime):
    """The stub daemon's line lands in worker.log while the process is still running.

    A redirected stdout is block-buffered, so a daemon that logs its shutdown feedback the
    instant it accepts SIGTERM can leave that line sitting in memory — which is how a clean
    stop turns into a teardown-gate failure, and how a real "exited without logging
    shutdown feedback" hides behind a flake. The stub deliberately never flushes.
    """
    runtime_root, port = worker_daemon_runtime
    env = {
        "POCKETBASE_URL": f"http://127.0.0.1:{port}",
        "APPIUM_URL": f"http://127.0.0.1:{port}",
    }
    result = _run(runtime_root, "worker.sh", "start", "--daemon", **env)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    daemon_pid = int((runtime_root / ".boss_agent" / "worker.pid").read_text().strip())
    try:
        recorded = _wait_for(runtime_root / ".boss_agent" / "worker.log", "UNFLUSHED_DAEMON_MARKER")
        assert "UNFLUSHED_DAEMON_MARKER" in recorded, (
            "the daemon's log line never reached the file while the daemon was running; "
            f"its stdout is block-buffered. worker.log:\n{recorded}"
        )
        assert _process_alive(daemon_pid), "the marker only appeared because the daemon exited"
    finally:
        _run(runtime_root, "worker.sh", "stop", **env)
        if _process_alive(daemon_pid):
            with contextlib.suppress(OSError):
                os.kill(daemon_pid, 9)
    assert not _process_alive(daemon_pid), "the stub daemon outlived the test"


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    ps = shutil.which("ps")
    if ps is None:
        return True
    state = subprocess.run(
        [ps, "-p", str(pid), "-o", "stat="], capture_output=True, text=True, check=False
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


def _kill_process_group(process: subprocess.Popen) -> None:
    """Take down a runner and everything it started, without reaching anything older.

    A runner that blocks on a boot wait has to be killed, and killing only the direct child
    would leave the adb queries and stub binaries it spawned behind.
    """
    if process.poll() is not None:
        return
    with contextlib.suppress(OSError):
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=5)
    if process.poll() is None:
        with contextlib.suppress(OSError):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)


def test_worker_restart_records_completion_when_the_new_daemon_starts(worker_daemon_runtime):
    """A restart that actually starts records its completion, not just its intent.

    The stop half is the same seam `stop` uses; this covers the half that only a daemon
    restart reaches, where the service is genuinely back rather than merely asked to stop.
    """
    runtime_root, port = worker_daemon_runtime
    env = {
        "POCKETBASE_URL": f"http://127.0.0.1:{port}",
        "APPIUM_URL": f"http://127.0.0.1:{port}",
    }
    assert _run(runtime_root, "worker.sh", "start", "--daemon", **env).returncode == 0

    log_file = runtime_root / ".boss_agent" / "worker.log"
    result = _run(runtime_root, "worker.sh", "restart", "--daemon", **env)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    recorded = _read(log_file)
    for event in (RESTART_REQUESTED, STOP_REQUESTED, STOP_COMPLETED, RESTART_COMPLETED):
        assert event in recorded, f"restart did not record {event!r} in worker.log:\n{recorded}"
