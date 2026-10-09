"""
tests/e2e/test_emulator_serial_and_restart.py
============================================
AVD serial resolution, shutdown ordering and the `restart` lifecycle (ticket #365).

Three defects, one shared root cause. `emulator.sh` resolved *the* serial for the dedicated
AVD by taking whichever matching transport `adb devices` happened to list first, and once it
had a serial it tore the bridge down *before* asking the emulator to stop:

* The Remote ADB Bridge forwards to the same adbd, so its `<lan-ip>:<port>` transport reports
  the same AVD name as the local emulator. When the bridge was listed first, that TCP
  transport won resolution — and `emu kill` is an emulator-console command that means nothing
  on a TCP transport, so `stop` silently did nothing and the AVD outlived it.
* Tearing the bridge down first removed the very transport the stop depended on, so the
  emulator's exit negotiation could not complete.
* The Android emulator is a front-end over a QEMU backend, so the process that actually holds
  the AVD is `qemu-system-*` with `-avd <name>` in its argv — a spelling the single
  `emulator.*@<name>` pattern never matched, leaving the QEMU process orphaned.

The real `emulator.sh` is copied into a throwaway runtime root and driven against scripted
fakes (see `_runner_harness.py`), so these assert observable behaviour — resolved serial,
recorded call ordering, real process liveness — without touching the shared AVD, hence no
`live` marker.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import pytest
from _runner_harness import BASH, FAKE_LAN_IP, TARGET_AVD, RunnerScriptHarness

from _service_harness import REPO_ROOT, wait_until_dead

pytestmark = pytest.mark.e2e

NATIVE_SERIAL = "emulator-5554"

# The SDK's own emulator binary, used only to enumerate the AVDs really installed here.
ANDROID_HOME = Path(
    os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or ""
).expanduser()

# `status` is bounded by contract, and `STATUS_BUDGET_SEC` is that bound. A `restart` is a
# different order of operation — it stops the AVD, launches a replacement, waits for the boot,
# spawns a bridge daemon and waits for the LAN transport — so it needs a budget of its own.
# The value is not a latency target: it exists to catch a *lockup* (the no-`--daemon` path
# parks on `tail -f` forever), and any finite bound catches that just as well.
RESTART_BUDGET_SEC = 45.0

# An AVD name no real emulator can carry. Anything that reaches a process-pattern `pkill`
# must name this, or the suite would signal the developer's real AVD. Same value the harness
# itself uses, so a test that forgets to override `ANDROID_AVD` is still isolated.
HARNESS_AVD = TARGET_AVD

# The argv a real AVD leaves behind, per host. The `emulator` front-end is what the script
# launches; the QEMU backend is the process that actually owns the AVD. macOS ships
# `qemu-system-aarch64-headless` under the SDK, Linux ships `qemu-system-x86_64`. Neither
# carries the `emulator ... @<avd>` spelling, which is the point.
FRONTEND_ARGV = ("emulator", f"@{HARNESS_AVD}", "-no-snapshot-load")
MACOS_QEMU_ARGV = ("qemu-system-aarch64-headless", "-avd", HARNESS_AVD, "-no-snapshot-load")
LINUX_QEMU_ARGV = ("qemu-system-x86_64", "-avd", HARNESS_AVD)

# A real process has to wear these command lines, so each is spawned through the interpreter
# with the target argv appended: `ps`/`pgrep` report the whole argv, and the interpreter is
# the only thing guaranteed to be on PATH.
SLEEP = "import time; time.sleep(600)"


@pytest.fixture
def runner(tmp_path: Path) -> RunnerScriptHarness:
    harness = RunnerScriptHarness(tmp_path)
    yield harness
    harness.shutdown()


def lan_serial(runner: RunnerScriptHarness) -> str:
    """The transport the Remote ADB Bridge publishes for this scenario."""
    return f"{FAKE_LAN_IP}:{runner.bridge_port}"


def spawn_wearing(runner: RunnerScriptHarness, argv: Sequence[str]) -> subprocess.Popen[bytes]:
    """Spawn a live process whose `ps` command line contains `argv` verbatim."""
    return runner.spawn_process([sys.executable, "-c", SLEEP, *argv])


def assert_reaped(process: subprocess.Popen[bytes], label: str, output: str) -> None:
    """The process has terminated.

    `wait_until_dead` polls `Popen`, which reaps the child. A raw pid liveness check would
    not: a SIGTERMed child stays a zombie until the parent reaps it, so it would read as
    alive forever and the assertion would be about pytest's bookkeeping, not about `stop`.
    (The script's own view is the production one — `runner_process_alive` treats a zombie as
    gone, deliberately.)
    """
    assert wait_until_dead(process, timeout=5.0), f"{label} survived:\n{output}"


# ---------------------------------------------------------------------------
# Serial resolution must lock onto the local native emulator
# ---------------------------------------------------------------------------
def test_native_emulator_wins_when_the_bridge_endpoint_is_listed_first(
    runner: RunnerScriptHarness,
):
    """AC: with the native emulator and the bridge port both online, resolution is stable.

    `adb devices` order is not a contract, and both transports resolve to the same AVD name,
    so a resolver that takes "the first match" is at the mercy of that order. The native
    transport is the only one `emu kill` works on, so it has to win regardless of position.
    """
    bridge = lan_serial(runner)
    runner.script(
        devices=[(bridge, "device"), (NATIVE_SERIAL, "device")],
        avd_name=TARGET_AVD,
        # The bridge forwards to the same adbd, so its transport reports the same AVD name
        # and matches just as well as the native one.
        serial_avd_names={bridge: TARGET_AVD},
        boot_completed="1",
    )

    result = runner.run("status")

    assert result.returncode == 0, f"status failed:\n{result.stdout}\n{result.stderr}"
    assert f"ONLINE and READY ({NATIVE_SERIAL})" in result.stdout, (
        f"the bridge endpoint was chosen over the native emulator:\n{result.stdout}"
    )
    # And the losing candidate must not even be interrogated: a wedged or half-open bridge
    # transport must not be able to delay, or win, the resolution of a healthy local AVD.
    assert "ro.boot.qemu.avd_name" not in runner.calls(), runner.calls()


def test_lan_bridge_endpoint_still_resolves_when_it_is_the_only_transport(
    runner: RunnerScriptHarness,
):
    """Positive control for the priority rule above.

    Without this, an implementation that simply stopped looking at LAN endpoints would pass
    the priority test while breaking the LAN-only case that `ensure_lan_adb_connected` and
    remote management exist to provide.
    """
    bridge = lan_serial(runner)
    runner.script(
        devices=[(bridge, "device")],
        serial_avd_names={bridge: TARGET_AVD},
        boot_completed="1",
    )

    result = runner.run("status")

    assert result.returncode == 0, f"status failed:\n{result.stdout}\n{result.stderr}"
    assert f"ONLINE and READY ({bridge})" in result.stdout, result.stdout


def test_stop_reports_the_avd_as_still_running_when_a_bridge_is_also_live(
    runner: RunnerScriptHarness,
):
    """AC: `stop` names the native transport, and leaves the AVD behind it running.

    Serial resolution still has to lock onto the local native emulator rather than the
    bridge's TCP endpoint — the LAN transport is only there while the bridge is, so
    resolving to it would let a bridge outage make a healthy AVD look absent. What changed
    is what `stop` then *does* with that serial: nothing at all to the device.
    """
    bridge = lan_serial(runner)
    runner.script(
        devices=[(bridge, "device"), (NATIVE_SERIAL, "device")],
        avd_name=TARGET_AVD,
        serial_avd_names={bridge: TARGET_AVD},
        boot_completed="1",
    )

    result = runner.run("stop")

    assert result.returncode == 0, f"stop failed:\n{result.output}"
    assert f"still running ({NATIVE_SERIAL})" in result.stdout, result.stdout
    assert "emu kill" not in runner.calls(), (
        f"`stop` still sends a kill to the device:\n{runner.calls()}"
    )


# ---------------------------------------------------------------------------
# Shutdown releases the services; no command in this runner stops the AVD
# ---------------------------------------------------------------------------
# The AVD is machine-wide infrastructure whose next boot costs 30-60s, so a shutdown signal
# from this runner releases the Remote ADB Bridge and nothing else. There is no flag that
# escalates it: a Virtual Device Session is the user's to close, by hand, when they mean it.
#
# The assertions below are deliberately blunt — "the process is still running" — because the
# failure they guard against is a machine-wide one. `pgrep -f` reaches every emulator on the
# box, and every AVD on it belongs to some worktree, so a pattern that drifts back into
# matching costs other developers their devices.


@pytest.mark.parametrize(
    "qemu_argv",
    [MACOS_QEMU_ARGV, LINUX_QEMU_ARGV],
    ids=["macos-qemu", "linux-qemu"],
)
def test_stop_never_kills_the_avd_process(runner: RunnerScriptHarness, qemu_argv: Sequence[str]):
    """`stop` must leave both emulator process shapes running, on macOS and Linux.

    Both shapes matter: the `emulator ... @<avd>` front-end and the `qemu-system-*` backend
    that actually owns the AVD. A sweep matching only the front-end's spelling is exactly
    how a QEMU process was orphaned in the first place.
    """
    runner.script(devices=[])
    qemu = spawn_wearing(runner, qemu_argv)
    frontend = spawn_wearing(runner, FRONTEND_ARGV)

    result = runner.run("stop", env={"ANDROID_AVD": HARNESS_AVD})

    assert result.returncode == 0, result.output
    assert qemu.poll() is None, f"`stop` killed the QEMU backend {qemu_argv[0]} (PID {qemu.pid})"
    assert frontend.poll() is None, f"`stop` killed the emulator front-end (PID {frontend.pid})"


def test_stop_sends_no_kill_to_any_transport(runner: RunnerScriptHarness):
    """`emu kill` is a console command aimed at the AVD; nothing in this runner sends it.

    Kept as its own test because it is the cheapest possible detector of the old behaviour
    coming back: a `stop` that reaches for the emulator console is, by definition, one that
    intends to end the device, and this runner never does.
    """
    runner.script(devices=[(NATIVE_SERIAL, "device")], avd_name=TARGET_AVD, boot_completed="1")

    result = runner.run("stop")

    assert result.returncode == 0, f"stop failed:\n{result.output}"
    assert "emu kill" not in runner.calls(), runner.calls()


def test_stop_never_kills_an_avd_with_no_device_behind_it(runner: RunnerScriptHarness):
    """A process sweep keyed on a device that adb cannot see must not become the fallback.

    This is where the old implementation reached for `emu kill` and a `pgrep` sweep: when no
    transport resolved, it still went looking for emulator processes on the machine. With
    nothing to signal, the correct amount of work is none.
    """
    runner.script(devices=[])
    qemu = spawn_wearing(runner, MACOS_QEMU_ARGV)

    result = runner.run("stop", env={"ANDROID_AVD": HARNESS_AVD})

    assert result.returncode == 0, result.output
    assert qemu.poll() is None, (
        f"`stop` signalled an emulator process it had no device for (PID {qemu.pid})"
    )
    assert "Stopped emulator processes" not in result.stdout, result.stdout
    assert "Reaped" not in result.stdout, result.stdout


def test_stop_never_signals_an_emulator_belonging_to_another_avd(runner: RunnerScriptHarness):
    """`pgrep -f` is machine-wide: an unscoped pattern would hit every other worktree's AVD."""
    runner.script(devices=[])
    bystander = spawn_wearing(
        runner, ("qemu-system-aarch64-headless", "-avd", "some_other_avd", "-no-snapshot-load")
    )

    result = runner.run("stop", env={"ANDROID_AVD": HARNESS_AVD})

    assert bystander.poll() is None, f"`stop` killed another AVD's emulator:\n{result.stdout}"


def test_stop_never_trusts_a_recycled_pid_in_the_pidfile(runner: RunnerScriptHarness):
    """`emulator.pid` outlives the process it names, so its PID gets reused.

    A bystander wearing the AVD name in its own command line — a developer's `grep`, say —
    is what any implementation reading that pidfile has to refuse to signal.
    """
    runner.script(devices=[])
    bystander = spawn_wearing(runner, ("grep", "-r", HARNESS_AVD, "/tmp"))
    pid_file = runner.runtime_root / ".boss_agent" / "emulator.pid"
    pid_file.write_text(f"{bystander.pid}\n", encoding="utf-8")

    result = runner.run("stop", env={"ANDROID_AVD": HARNESS_AVD})

    assert bystander.poll() is None, (
        f"`stop` signalled a recycled PID from emulator.pid:\n{result.stdout}"
    )


def test_stop_tears_the_bridge_down_and_frees_its_port(runner: RunnerScriptHarness):
    """The other half of "stops the services": what is stopped must actually be gone.

    Asserting only that the AVD survived would let a `stop` that stopped nothing at all pass,
    which is the shape of bug a teardown is supposed to catch.
    """
    runner.script(devices=[(NATIVE_SERIAL, "device")], avd_name=TARGET_AVD, boot_completed="1")
    bridge = runner.start_bridge()

    result = runner.run("stop")

    assert result.returncode == 0, result.output
    assert runner.call_index("bridge: signal 15") >= 0, (
        f"the bridge was never stopped:\n{result.output}"
    )
    assert bridge.wait(timeout=10) is not None, "the bridge process outlived `stop`"
    assert not runner.port_is_bound(), "the bridge is still holding its port after `stop`"


def test_stop_ignores_a_stray_force_flag_without_escalating(runner: RunnerScriptHarness):
    """A leftover `--force` from an old script or muscle memory must not stop the AVD.

    The flag is gone from the usage, but scripts and muscle memory outlive both. What matters
    is that an unrecognised argument is inert: the AVD survives, and the services still stop,
    so a stale invocation degrades to the plain contract instead of to an incident.
    """
    runner.script(devices=[(NATIVE_SERIAL, "device")], avd_name=HARNESS_AVD, boot_completed="1")
    qemu = spawn_wearing(runner, MACOS_QEMU_ARGV)
    bridge = runner.start_bridge()

    result = runner.run("stop", "--force", env={"ANDROID_AVD": HARNESS_AVD})

    assert result.returncode == 0, result.output
    assert qemu.poll() is None, f"a stray --force killed the AVD process {qemu.pid}"
    assert "emu kill" not in runner.calls(), runner.calls()
    assert bridge.wait(timeout=10) is not None, "the Remote ADB Bridge was never stopped"


# ---------------------------------------------------------------------------
# `restart`: a real stop/start cycle, not a fall-through to `start`
# ---------------------------------------------------------------------------
def test_restart_runs_the_full_stop_then_start_cycle(runner: RunnerScriptHarness):
    """AC: `restart` must execute stop *and* start when there is no AVD to reuse.

    `restart` used to fall through the dispatcher's catch-all arm, which ran `start` against
    the already-running instance: nothing was ever stopped, so the old bridge survived and
    the "restart" was a no-op wearing a restart's name.

    The scenario is an AVD that is *not* running — `adb` reports no device — so `restart`
    has a genuine cold boot to do. That is the only path that launches an emulator now; the
    reuse path deliberately does not, and the wedged-AVD path refuses to.
    """
    state_dir = runner.install_fake_emulator()
    runner.script(devices=[], avd_name=TARGET_AVD, boot_completed="1")
    bridge = runner.start_bridge()

    result = runner.run(
        "restart",
        "--daemon",
        budget=RESTART_BUDGET_SEC,
        env={"FAKE_EMULATOR_REGISTERS_DEVICE": "1", "ANDROID_AVD": TARGET_AVD},
    )

    assert result.returncode == 0, f"restart failed:\n{result.output}"
    stopped_at = runner.call_index("bridge: signal 15")
    started_at = runner.call_index("emulator: launched")
    assert stopped_at >= 0, f"restart never stopped the running services:\n{result.output}"
    assert started_at >= 0, f"restart never launched a replacement AVD:\n{result.output}"
    assert stopped_at < started_at, (
        f"the AVD was launched before the old services were stopped:\n{runner.calls()}"
    )
    assert bridge.wait(timeout=10) is not None, "the old Remote ADB Bridge was never terminated"
    assert "fake emulator launched" in runner.emulator_launch_log(state_dir)
    assert runner.port_is_bound(), "restart left the new AVD without its bridge"

    # The replacement has to end up *usable*, not merely launched: a restarted AVD that no
    # longer answers over the LAN is the failure this whole ticket exists to remove.
    status = runner.run("status")
    assert status.returncode == 0, f"restart left an unhealthy AVD:\n{status.output}"
    assert "Remote ADB Bridge is LISTENING" in status.stdout, status.stdout
    assert "LAN ADB Connection is CONNECTED" in status.stdout, status.stdout


def test_restart_daemon_does_not_attach_to_the_log_stream(runner: RunnerScriptHarness):
    """AC: `restart --daemon` must return instead of holding the terminal.

    The flag is threaded through to `start`; without it the runner attaches a `tail -f` and
    a scripted restart would never return at all.
    """
    runner.install_fake_emulator()
    runner.script(devices=[], avd_name=TARGET_AVD, boot_completed="1")

    result = runner.run(
        "restart",
        "--daemon",
        budget=RESTART_BUDGET_SEC,
        env={"FAKE_EMULATOR_REGISTERS_DEVICE": "1", "ANDROID_AVD": TARGET_AVD},
    )

    assert result.returncode == 0, f"restart --daemon failed:\n{result.output}"
    assert "Attaching to live log stream" not in result.stdout, (
        f"`--daemon` was dropped and the runner attached to logs:\n{result.stdout}"
    )
    # The point is that it *returns*: without `--daemon` this call parks on `tail -f` and
    # only ends when the harness kills it at the budget.
    assert result.elapsed < RESTART_BUDGET_SEC, (
        f"`restart --daemon` took {result.elapsed:.2f}s — it is holding the terminal"
    )


def test_restart_is_not_the_same_as_start(runner: RunnerScriptHarness):
    """The `stop` half of `restart` is the whole point, and must be observable.

    A cheaper guard than the full cycle above, and the one that fails first when the
    dispatcher loses its `restart` arm: with a bridge already up, `start` leaves it running.
    """
    runner.install_fake_emulator()
    runner.script(devices=[], avd_name=TARGET_AVD, boot_completed="1")
    runner.start_bridge()

    runner.run(
        "restart",
        "--daemon",
        budget=RESTART_BUDGET_SEC,
        env={"FAKE_EMULATOR_REGISTERS_DEVICE": "1"},
    )

    assert runner.call_index("bridge: signal 15") >= 0, (
        "`restart` behaved like `start`: the running bridge was never stopped"
    )


def test_restart_reuses_already_online_avd(runner: RunnerScriptHarness):
    """AC: when the dedicated AVD is online and booted, restart keeps it — no cold boot."""
    runner.script(
        devices=[(NATIVE_SERIAL, "device")],
        avd_name=TARGET_AVD,
        boot_completed="1",
    )
    runner.start_bridge()

    result = runner.run("restart", "--daemon")

    assert result.returncode == 0, f"restart failed:\n{result.output}"
    assert "emu kill" not in runner.calls(), (
        f"`restart` killed the online AVD instead of reusing it:\n{runner.calls()}"
    )
    assert "reusing" in result.stdout.lower() or "already" in result.stdout.lower(), result.stdout


def test_restart_restarts_the_bridge_without_cold_booting_the_avd(runner: RunnerScriptHarness):
    """AC: `restart` signals the *other* service this runner owns — the Remote ADB Bridge.

    The AVD is not the only service `emulator.sh` owns. A bridge left running across a
    restart is a bridge that was never restarted: it keeps serving a transport whose
    target process, its LAN endpoint and its remote peers have all moved on. So the reuse
    path skips the 30-60s cold boot and still sends the bridge a real shutdown signal,
    leaving `cmd_start` to bring a fresh one up.

    Both halves are asserted together because either alone is a different regression: the
    `emu kill` assertion is what stops this from quietly becoming a cold boot, and the
    `signal 15` assertion is what stops it from quietly becoming a no-op.
    """
    runner.script(
        devices=[(NATIVE_SERIAL, "device")],
        avd_name=TARGET_AVD,
        boot_completed="1",
    )
    bridge = runner.start_bridge()

    result = runner.run("restart", "--daemon")

    assert result.returncode == 0, f"restart failed:\n{result.output}"
    assert "emu kill" not in runner.calls(), (
        f"the AVD must be reused, not cold-booted:\n{runner.calls()}"
    )
    assert runner.call_index("bridge: signal 15") >= 0, (
        f"`restart` did not send the Remote ADB Bridge a shutdown signal:\n{runner.calls()}"
    )
    assert bridge.wait(timeout=10) is not None, "the old bridge process was never terminated"


def test_restart_never_kills_the_avd_process(runner: RunnerScriptHarness):
    """AC: the AVD process outlives a `restart`, and so does its log-watching terminal.

    Asserted as process liveness rather than as an absent call, so it holds even if the
    runner reaches the device by a route the call log never records. This is the assertion
    `--force` defeated three separate times: every earlier version of it ran `stop --force`
    in a fixture, so it was testing the flag, not the contract.
    """
    runner.script(
        devices=[(NATIVE_SERIAL, "device")],
        avd_name=HARNESS_AVD,
        boot_completed="1",
    )
    qemu = spawn_wearing(runner, MACOS_QEMU_ARGV)
    bridge = runner.start_bridge()

    result = runner.run("restart", "--daemon", env={"ANDROID_AVD": HARNESS_AVD})

    assert result.returncode == 0, f"restart failed:\n{result.output}"
    assert qemu.poll() is None, f"`restart` killed the AVD process {qemu.pid}"
    assert "emu kill" not in runner.calls(), runner.calls()
    assert bridge.wait(timeout=10) is not None, "the bridge was never stopped, so nothing restarted"


def test_the_reuse_notice_says_what_was_and_was_not_restarted(runner: RunnerScriptHarness):
    """The notice must name what *is* restarted, and that the AVD is merely kept.

    "restart sent no stop signal" is indistinguishable from a broken stop unless the runner
    says which service it signalled. Reuse is deliberate (#363), but so is the fact that
    something *was* restarted: an operator reading only the first line cannot tell a path
    that stopped nothing from one that stopped the bridge.
    """
    runner.script(
        devices=[(NATIVE_SERIAL, "device")],
        avd_name=TARGET_AVD,
        boot_completed="1",
    )
    runner.start_bridge()

    result = runner.run("restart", "--daemon")

    assert result.returncode == 0, f"restart failed:\n{result.output}"
    assert "emu kill" not in runner.calls(), (
        "the notice must describe the reuse path; this run actually killed the AVD"
    )
    assert "bridge" in result.stdout.lower(), (
        f"the reuse notice did not say the Remote ADB Bridge is what gets restarted:\n"
        f"{result.stdout}"
    )
    assert "no cold boot" in result.stdout.lower(), (
        f"the reuse notice did not say the AVD is kept rather than cold-booted:\n{result.stdout}"
    )


def test_the_usage_no_longer_advertises_a_force_flag():
    """No command stops the AVD, so the header must not offer a way to.

    Usage text is the interface, and an option that is documented but does nothing is worse
    than one that is absent: it promises a device shutdown that never arrives. Asserted
    against the shipped script rather than a copy of it, so the check cannot drift away
    from the file it describes — which is how `--force` kept coming back.
    """
    body = (REPO_ROOT / "emulator.sh").read_text(encoding="utf-8")

    assert "--force" not in body, "emulator.sh still mentions --force somewhere"
    assert "emu kill" not in body, "emulator.sh still reaches for the emulator console"


def test_the_harness_avd_name_cannot_be_a_real_avd():
    """The suite must never point a machine-wide process scan at a real emulator.

    This is the test that would have prevented an actual loss, so it is worth being blunt
    about what it guards. `emulator.sh` locates its AVD with a machine-wide `pgrep -f` over
    the AVD name, and the harness once handed every runner script the project's real
    `boss_avd_arm64`. Any test that did not override `ANDROID_AVD` therefore ran that scan
    against whatever the developer had actually running — and the suite killed a live
    emulator, taking its LAN bridge transport offline with it.

    Nothing in the fake `adb` can prevent this. A fake transport is not a fake *process*: the
    scan reads `ps`/`pgrep`, so only the AVD *name* can keep the two worlds apart. Hence this
    check reads the machine's real AVD list and fails if the constant is on it.

    A failure here is never "the test is wrong" — it means the next suite run may destroy a
    developer's emulator, so it must be read as a stop-the-line signal.
    """
    installed = set()
    try:
        proc = subprocess.run(
            [str(ANDROID_HOME / "emulator" / "emulator"), "-list-avds"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        installed = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    except (OSError, subprocess.SubprocessError):
        # No SDK on this machine (a Linux CI box, say): there is nothing it could collide
        # with, so the invariant is satisfied by construction.
        pytest.skip("no Android SDK on this machine, so no AVD can collide")

    assert TARGET_AVD not in installed, (
        f"the harness AVD name {TARGET_AVD!r} is a real AVD on this machine {installed}. "
        "A runner script's machine-wide process scan would signal the developer's emulator."
    )


# ---------------------------------------------------------------------------
# A shutdown signal from another terminal must unblock this one (#425)
# ---------------------------------------------------------------------------
# This is the scenario the runner is actually judged on, and it is two terminals: one holding
# an attached log stream, another issuing `stop` or `restart`. The watch only works when it is
# given something it can watch, and the handle that receives a shutdown signal is the Remote ADB
# Bridge — no command here stops the AVD, so a stream that watched only the AVD was watching the
# one process that never goes away and could never unblock.
#
# Every test below asserts on the attached process *exiting*, not on printed text. A watcher
# that announced the stop and then kept spinning would pass a text-only check, and the symptom
# users actually reported was a terminal that never returns.


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _wait_for_text(path: Path, needle: str, budget: float = 15.0) -> str:
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        text = _read(path)
        if needle in text:
            return text
        time.sleep(0.05)
    raise AssertionError(f"never saw {needle!r} in the attached stream:\n{_read(path)}")


def _attach_log_stream(runner: RunnerScriptHarness, captured: Path) -> subprocess.Popen:
    """Start terminal A: a runner holding an attached log stream, exactly as a user would."""
    with captured.open("w") as sink:
        return subprocess.Popen(
            [BASH, "emulator.sh", "logs"],
            cwd=str(runner.runtime_root),
            env=runner.command_env({"ANDROID_AVD": HARNESS_AVD}),
            stdout=sink,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def _kill_if_alive(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait(timeout=5)


def _require_ended(attached: subprocess.Popen, captured: Path, why: str) -> str:
    """Terminal A must return to the shell. A timeout here is the bug, not a flake."""
    try:
        attached.wait(timeout=20)
    except subprocess.TimeoutExpired:
        out = _read(captured)
        _kill_if_alive(attached)
        raise AssertionError(f"the attached stream never ended {why}:\n{out}") from None
    return _read(captured)


@pytest.mark.parametrize("command", ["stop", "restart"], ids=["stop", "restart"])
def test_a_stop_or_restart_from_another_terminal_ends_the_attached_stream(
    runner: RunnerScriptHarness,
    command: str,
):
    """AC: the two-terminal path — A attached, B signals, A returns, the AVD keeps running.

    Both commands are covered because they reach the signal by different routes: `stop` is
    pure `cmd_stop`, while `restart` on the reuse path tears the bridge down itself and then
    re-attaches. A watch that only understood one of them would leave the other terminal
    hanging, which is the exact symptom this pins down.

    The AVD's survival is asserted in the same test on purpose. "A returned" and "the device
    is still there" are the two halves of the contract, and a change that made A return by
    killing the AVD would satisfy a liveness-only check.
    """
    runner.script(devices=[(NATIVE_SERIAL, "device")], avd_name=HARNESS_AVD, boot_completed="1")
    qemu = spawn_wearing(runner, MACOS_QEMU_ARGV)
    runner.start_bridge()

    captured = runner.runtime_root / "attached.out"
    attached = _attach_log_stream(runner, captured)
    try:
        _wait_for_text(captured, "Attaching to live log stream")

        # Terminal A is following a live service: it must not have returned early.
        with pytest.raises(subprocess.TimeoutExpired):
            attached.wait(timeout=1.0)

        result = runner.run(command, "--daemon", env={"ANDROID_AVD": HARNESS_AVD})
        assert result.returncode == 0, f"{command} failed:\n{result.output}"

        out = _require_ended(attached, captured, f"after another terminal ran `{command}`")
    finally:
        _kill_if_alive(attached)

    assert "已停止" in out, f"the stream ended without announcing the stop:\n{out}"
    assert qemu.poll() is None, f"`{command}` killed the AVD process {qemu.pid}"
    assert "emu kill" not in runner.calls(), runner.calls()


def test_a_plain_start_from_another_terminal_disturbs_nothing(runner: RunnerScriptHarness):
    """AC: a bare `./emulator.sh` only attaches — it must not signal any running service.

    The mirror of the two-terminal stop tests, and the one that keeps the default honest.
    Attaching is a read: the second terminal wants the same log stream, not a new decision
    about the box. So a running bridge has to survive it, and terminal A has to still be
    attached when B walks in — otherwise `restart` and `stop` stop being the *only* commands
    that close a service, and the lifecycle becomes unpredictable from the terminal.

    This is a regression guard on a real mechanism rather than a hypothetical one: reaching
    the attach path goes through `start_remote_bridge`, which reclaims port 6555 from any
    `python`/`socat` it finds there when it cannot identify a running bridge. A plain start
    must never be the thing that triggers that.
    """
    runner.script(devices=[(NATIVE_SERIAL, "device")], avd_name=HARNESS_AVD, boot_completed="1")
    bridge = runner.start_bridge()

    captured = runner.runtime_root / "attached-a.out"
    attached = _attach_log_stream(runner, captured)
    try:
        _wait_for_text(captured, "Attaching to live log stream")

        # Terminal B: the default invocation, which is "attach" and nothing else.
        second = runner.spawn_script("emulator.sh")
        try:
            time.sleep(3.0)

            assert runner.call_index("bridge: signal") < 0, (
                f"a plain `start` sent a shutdown signal to a running service:\n{runner.calls()}"
            )
            assert "emu kill" not in runner.calls(), runner.calls()
            assert bridge.poll() is None, f"a plain `start` killed the bridge {bridge.pid}"
            assert attached.poll() is None, (
                "a plain `start` from another terminal ended the first one's stream:\n"
                + _read(captured)
            )
        finally:
            if second.poll() is None:
                os.killpg(os.getpgid(second.pid), signal.SIGKILL)
                second.wait(timeout=5)
    finally:
        _kill_if_alive(attached)

    assert bridge.poll() is None, "the bridge did not survive a second, plain terminal"


def test_a_plain_start_never_signals_a_process_it_does_not_own(runner: RunnerScriptHarness):
    """AC: attaching must not kill a stranger that happens to hold the bridge's port.

    The concrete mechanism behind "a plain `./emulator.sh` closed another service". The port
    reclaim used to identify its victim by `ps -o comm=` being `python` or `socat` — and the
    bridge's own `comm` is merely `python3`, so that test identifies nothing in particular.
    A plain start, whose only job is to attach, could then SIGTERM and SIGKILL whatever was
    listening on 6555: another worktree's bridge, an unrelated Python service, anything.

    So the fixture here is a process that is deliberately *not* identifiable as a bridge, and
    the assertion is that it is still running afterwards. The harness keeps the port free, so
    the port is taken by hand to make the reclaim path the one under test.
    """
    runner.script(devices=[(NATIVE_SERIAL, "device")], avd_name=HARNESS_AVD, boot_completed="1")

    # A listener on the bridge port whose command line gives no reason to touch it.
    squatter = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import socket,sys;"
            "s=socket.socket();"
            "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
            f"s.bind(('127.0.0.1',{runner.bridge_port}));"
            "s.listen(5);"
            "print('ready',flush=True);"
            "sys.stdin.read()",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        # Synchronise on the child's own "ready": checking the port before it binds would
        # make the reclaim path untested while still looking like it passed.
        assert squatter.stdout.readline().strip() == "ready", "the squatter never came up"
        assert runner.port_is_bound(), "the squatter never took the bridge port"

        result = runner.run("start", "--daemon", env={"ANDROID_AVD": HARNESS_AVD})

        assert squatter.poll() is None, (
            f"a plain `start` killed PID {squatter.pid}, a process it does not own:\n"
            f"{result.output}"
        )
        assert "Reclaiming" not in result.stdout, (
            f"a plain `start` treated a stranger as reclaimable:\n{result.stdout}"
        )
    finally:
        if squatter.poll() is None:
            squatter.kill()
        squatter.wait(timeout=5)


def test_the_stop_notice_names_the_bridge_that_was_signalled(
    runner: RunnerScriptHarness,
):
    """The notice must name the service that was actually stopped.

    A stop notice reading "已停止 (PID: n)" is only meaningful next to a name. And the name
    matters here more than usual: the AVD is still running, so an operator who reads an
    anonymous PID has no way to tell whether the device went down — which is the confusion
    that made the previous behaviour look like a broken teardown.
    """
    runner.script(devices=[(NATIVE_SERIAL, "device")], avd_name=HARNESS_AVD, boot_completed="1")
    bridge = runner.start_bridge()

    captured = runner.runtime_root / "attached.out"
    attached = _attach_log_stream(runner, captured)
    try:
        _wait_for_text(captured, "Attaching to live log stream")
        runner.run("stop", env={"ANDROID_AVD": HARNESS_AVD})
        out = _require_ended(attached, captured, "after another terminal ran `stop`")
    finally:
        _kill_if_alive(attached)

    assert "Remote ADB Bridge" in out, f"the notice did not name the Remote ADB Bridge:\n{out}"
    assert str(bridge.pid) in out, f"the notice did not name the bridge's PID {bridge.pid}:\n{out}"


def test_the_two_terminal_notice_names_the_service_that_was_signalled(
    runner: RunnerScriptHarness,
):
    """AC: the notice names the process that actually received the shutdown signal.

    This is the root cause, stated as an assertion. The stream used to be wired to the AVD's
    PID — the one process no command here stops — so a `stop` or `restart` from another
    terminal changed nothing it could see, and the terminal never returned. Naming the
    Remote ADB Bridge is the observable consequence of watching the right handle, and it is
    the part an operator can act on: the device is still up, so the thing that went away is
    the LAN transport, not the AVD.

    Deliberately *not* asserting a stop-and-restart announcement. Terminal A sees its bridge
    die and is entitled to say so; whether the other terminal is about to build a new one is
    not something it can know, and inferring it from a pidfile that has not been rewritten yet
    would be exactly the invented evidence `runner_restart_replacement_pid` refuses to invent.
    """
    runner.script(devices=[(NATIVE_SERIAL, "device")], avd_name=HARNESS_AVD, boot_completed="1")
    bridge = runner.start_bridge()
    qemu = spawn_wearing(runner, MACOS_QEMU_ARGV)

    captured = runner.runtime_root / "attached.out"
    attached = _attach_log_stream(runner, captured)
    try:
        _wait_for_text(captured, "Attaching to live log stream")
        result = runner.run("restart", "--daemon", env={"ANDROID_AVD": HARNESS_AVD})
        assert result.returncode == 0, f"restart failed:\n{result.output}"
        out = _require_ended(attached, captured, "after another terminal ran `restart`")
    finally:
        _kill_if_alive(attached)

    assert "Remote ADB Bridge" in out, (
        f"the notice did not name the Remote ADB Bridge, so the watch is not on the\n"
        f"process that receives a shutdown signal:\n{out}"
    )
    assert str(bridge.pid) in out, f"the notice did not name the bridge's PID {bridge.pid}:\n{out}"
    # Scoped to the stop notice, not the whole output: the pre-attach banner legitimately
    # prints the AVD's PID, since the AVD is the device the stream is following. What must
    # never happen is the AVD being named as the thing that *stopped*.
    notices = [line for line in out.splitlines() if "已停止" in line or "新 PID" in line]
    assert notices, f"the stream ended without a stop notice:\n{out}"
    assert all(str(qemu.pid) not in line for line in notices), (
        f"the stop notice named the AVD, which nothing in this runner stops:\n{out}"
    )
    assert qemu.poll() is None, f"`restart` killed the AVD process {qemu.pid}"


def test_the_attached_stream_also_ends_when_the_avd_dies_on_its_own(
    runner: RunnerScriptHarness,
):
    """The AVD is the writer of the log being displayed, so its death ends the stream too.

    Now that the AVD is the *only* thing a user can close by hand, this is the only path by
    which the display's subject can go away. Leaving the stream following a file nobody
    writes any more is the inert-cursor failure #425 was filed about, just reached from the
    other direction.

    Asserted on process exit rather than on text: the failure is a terminal that never
    returns, and a watcher that printed its notice and kept spinning would pass a text check.
    """
    runner.script(devices=[(NATIVE_SERIAL, "device")], avd_name=HARNESS_AVD, boot_completed="1")
    # No pidfile is ever written here — this is the reused-AVD shape, where `cmd_start`
    # never reaches the launch path that records one.
    assert not (runner.runtime_root / ".boss_agent" / "emulator.pid").exists()
    avd = spawn_wearing(runner, ["qemu-system-aarch64-headless", "-avd", HARNESS_AVD])
    runner.start_bridge()

    captured = runner.runtime_root / "attached.out"
    attached = _attach_log_stream(runner, captured)
    try:
        _wait_for_text(captured, "Attaching to live log stream")
        with pytest.raises(subprocess.TimeoutExpired):
            attached.wait(timeout=1.0)

        avd.kill()
        wait_until_dead(avd)
        out = _require_ended(attached, captured, "after the AVD process died")
    finally:
        _kill_if_alive(attached)

    assert "已停止" in out, f"the stream ended without announcing the stop:\n{out}"
    assert str(avd.pid) in out, f"the notice did not name the AVD's PID {avd.pid}:\n{out}"


def test_the_attached_stream_still_refuses_to_invent_a_stop_without_an_avd(
    runner: RunnerScriptHarness,
):
    """No pidfile, no live AVD process and no bridge is still no information — never a death.

    The counterpart to the fallbacks above: widening what the watch will accept must not
    widen it into claiming a stop for a service this process cannot see. The fake `adb` still
    reports the device, so `logs` attaches — but with neither process behind it there is
    nothing to watch, and the stream must keep following rather than announce a stop.
    """
    runner.script(devices=[(NATIVE_SERIAL, "device")], avd_name=HARNESS_AVD, boot_completed="1")

    captured = runner.runtime_root / "attached.out"
    attached = _attach_log_stream(runner, captured)
    try:
        _wait_for_text(captured, "Attaching to live log stream")

        with pytest.raises(subprocess.TimeoutExpired):
            attached.wait(timeout=2.0)
        assert attached.poll() is None, "the stream exited with no service behind it"

        out = _read(captured)
        assert "已停止" not in out, (
            f"a stop was announced for a service with no process behind it:\n{out}"
        )
    finally:
        _kill_if_alive(attached)


# ---------------------------------------------------------------------------
# `stop` and `restart` stop the services and never the AVD process
# ---------------------------------------------------------------------------
# The AVD is machine-wide infrastructure and a cold boot costs 30-60s, so a shutdown signal
# releases the services this runner owns without charging that. There is no flag that stops
# the AVD: a Virtual Device Session is the user's to close, by hand, when they mean it.


@pytest.mark.parametrize(
    "qemu_argv",
    [MACOS_QEMU_ARGV, LINUX_QEMU_ARGV],
    ids=["macos-qemu", "linux-qemu"],
)
def test_restart_keeps_a_wedged_avd_and_refuses_to_boot_a_second_one(
    runner: RunnerScriptHarness,
    qemu_argv: Sequence[str],
):
    """A running-but-not-ready AVD is preserved, and no duplicate is booted beside it.

    `cmd_start` only reuses a device that reported `sys.boot_completed=1`, so without the
    guard below a wedged AVD would leave the start path free to launch a second emulator —
    two processes contending for one device. Preserving the process is the contract, so the
    command refuses and says who can lift the constraint: the user, by quitting the emulator.
    """
    runner.install_fake_emulator()
    runner.script(
        devices=[(NATIVE_SERIAL, "device")],
        avd_name=HARNESS_AVD,
        boot_completed="",
    )
    wedged = spawn_wearing(runner, qemu_argv)

    result = runner.run("restart", "--daemon", env={"ANDROID_AVD": HARNESS_AVD})

    assert result.returncode != 0, f"`restart` refused to act on a wedged AVD:\n{result.stdout}"
    assert wedged.poll() is None, f"`restart` killed the wedged AVD process {wedged.pid}"
    assert (
        "hand" in result.stdout.lower()
        or "退出" in result.stdout
        or "quit" in result.stdout.lower()
    ), f"the refusal did not say how to clear the wedge:\n{result.stdout}"
    assert runner.call_index("emulator --avd") < 0, (
        f"`restart` launched a second emulator beside the wedged one:\n{runner.calls()}"
    )


def test_restart_from_second_terminal_unblocks_bare_start_without_relaunching(
    runner: RunnerScriptHarness,
):
    """AC: When terminal A is attached via bare `./emulator.sh` (cmd_start) and terminal B
    runs `./emulator.sh restart`, terminal A must exit cleanly without trying to launch a
    second emulator, and terminal B must stay attached to the running AVD.
    """
    runner.script(devices=[(NATIVE_SERIAL, "device")], avd_name=HARNESS_AVD, boot_completed="1")
    qemu = spawn_wearing(runner, MACOS_QEMU_ARGV)
    runner.start_bridge()

    captured_a = runner.runtime_root / "attached-a.out"
    with captured_a.open("w") as sink_a:
        attached = subprocess.Popen(
            [BASH, "emulator.sh"],
            cwd=str(runner.runtime_root),
            env=runner.command_env({"ANDROID_AVD": HARNESS_AVD}),
            stdout=sink_a,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    try:
        _wait_for_text(captured_a, "Attaching to live log stream")

        captured_b = runner.runtime_root / "attached-b.out"
        with captured_b.open("w") as sink_b:
            second = subprocess.Popen(
                [BASH, "emulator.sh", "restart"],
                cwd=str(runner.runtime_root),
                env=runner.command_env({"ANDROID_AVD": HARNESS_AVD}),
                stdout=sink_b,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        try:
            # Terminal A must unblock and exit cleanly (returncode 0)
            out_a = _require_ended(attached, captured_a, "after terminal B ran restart")
            assert attached.returncode == 0, (
                f"Terminal A exited with {attached.returncode}:\n{out_a}"
            )
            assert "Starting Dedicated AVD" not in out_a, (
                f"Terminal A attempted to start another emulator:\n{out_a}"
            )
            assert "Error: Android 'emulator' binary not found" not in out_a, (
                f"Terminal A fell through to emulator binary check:\n{out_a}"
            )

            # Terminal B must stay alive and attached to logs
            _wait_for_text(captured_b, "Attaching to live log stream")
            time.sleep(1.0)
            assert second.poll() is None, f"Terminal B exited prematurely:\n{_read(captured_b)}"
            assert qemu.poll() is None, "The AVD process was killed"
        finally:
            _kill_if_alive(second)
    finally:
        _kill_if_alive(attached)
