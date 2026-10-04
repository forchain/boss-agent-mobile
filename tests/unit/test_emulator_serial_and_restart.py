"""
tests/unit/test_emulator_serial_and_restart.py
==============================================
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

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from _runner_harness import (
    FAKE_LAN_IP,
    STATUS_BUDGET_SEC,
    TARGET_AVD,
    RunnerScriptHarness,
)

from _service_harness import wait_until_dead

NATIVE_SERIAL = "emulator-5554"

# An AVD name no real emulator can carry. Anything that reaches a process-pattern `pkill`
# must name this, or the suite would signal the developer's real AVD.
HARNESS_AVD = "boss_avd_harness_only"

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


def test_stop_targets_the_native_transport_when_a_bridge_is_also_live(
    runner: RunnerScriptHarness,
):
    """AC: the stop must act on a transport where `emu kill` means something.

    This is the defect the ticket was filed against, stated as behaviour: the kill has to be
    addressed to the local emulator, not to the bridge's TCP endpoint.
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
    assert f"{NATIVE_SERIAL} emu kill" in runner.calls(), runner.calls()
    assert f"{bridge} emu kill" not in runner.calls(), (
        "`emu kill` was sent to the bridge endpoint, where it is not a console command"
    )


# ---------------------------------------------------------------------------
# Shutdown: reap the emulator's real processes, then close the bridge
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "qemu_argv",
    [MACOS_QEMU_ARGV, LINUX_QEMU_ARGV],
    ids=["macos-qemu", "linux-qemu"],
)
def test_stop_reaps_the_qemu_backend_process(runner: RunnerScriptHarness, qemu_argv: Sequence[str]):
    """AC: stopping must terminate the emulator, QEMU backend included, on macOS and Linux.

    The AVD outliving its runner is the failure that matters here: it keeps holding the
    hardware resources every other worktree shares, and `start` then finds a half-dead
    instance it cannot explain.
    """
    runner.script(devices=[])
    qemu = spawn_wearing(runner, qemu_argv)
    frontend = spawn_wearing(runner, FRONTEND_ARGV)

    result = runner.run("stop", env={"ANDROID_AVD": HARNESS_AVD})

    assert_reaped(frontend, f"the emulator front-end (PID {frontend.pid})", result.output)
    assert_reaped(qemu, f"the QEMU backend {qemu_argv[0]} (PID {qemu.pid})", result.output)


def test_stop_reports_the_processes_it_reaped(runner: RunnerScriptHarness):
    """A sweep that found something has to name it.

    The old fallback printed "Stopped emulator processes" unconditionally, whether or not it
    matched anything, so a pattern that quietly stopped matching was indistinguishable from a
    clean stop. Naming the reaped PIDs makes a no-op sweep visibly a no-op.
    """
    runner.script(devices=[])
    qemu = spawn_wearing(runner, MACOS_QEMU_ARGV)

    result = runner.run("stop", env={"ANDROID_AVD": HARNESS_AVD})

    assert str(qemu.pid) in result.stdout, (
        f"`stop` reaped PID {qemu.pid} without reporting it:\n{result.stdout}"
    )
    assert_reaped(qemu, f"the QEMU backend (PID {qemu.pid})", result.output)


def test_stop_does_not_claim_a_reap_it_did_not_perform(runner: RunnerScriptHarness):
    """The mirror of the above: with nothing to reap, no PIDs may be invented."""
    runner.script(devices=[])

    result = runner.run("stop", env={"ANDROID_AVD": HARNESS_AVD})

    assert "Stopped emulator processes" in result.stdout, result.stdout
    assert "Reaped" not in result.stdout, (
        f"`stop` reported a reap that never happened:\n{result.stdout}"
    )


def test_stop_never_reaps_an_emulator_belonging_to_another_avd(
    runner: RunnerScriptHarness,
):
    """The pattern sweep must be scoped to the target AVD.

    `pgrep -f` is a machine-wide operation: an unscoped pattern would SIGTERM every emulator
    the developer's other worktrees are running.
    """
    runner.script(devices=[])
    bystander = spawn_wearing(
        runner, ("qemu-system-aarch64-headless", "-avd", "some_other_avd", "-no-snapshot-load")
    )

    result = runner.run("stop", env={"ANDROID_AVD": HARNESS_AVD})

    assert bystander.poll() is None, (
        f"`stop` killed an emulator for a different AVD:\n{result.stdout}"
    )


def test_stop_never_trusts_a_recycled_pid_in_the_pidfile(runner: RunnerScriptHarness):
    """`emulator.pid` outlives the process it names, so its PID gets reused.

    The tempting shortcut — trust the pidfile because "we wrote it" — turns `stop` into a
    machine-wide hazard the moment the emulator has been gone long enough for the OS to hand
    its PID to something else. The bystander here mentions the AVD name in its command line,
    which is exactly the case a loose substring test would get wrong.
    """
    runner.script(devices=[])
    # A real process, wearing the AVD name in its argv, that is emphatically not an emulator.
    bystander = spawn_wearing(runner, ("grep", "-r", HARNESS_AVD, "/tmp"))
    pid_file = runner.runtime_root / ".boss_agent" / "emulator.pid"
    pid_file.write_text(f"{bystander.pid}\n", encoding="utf-8")

    result = runner.run("stop", env={"ANDROID_AVD": HARNESS_AVD})

    assert bystander.poll() is None, (
        f"`stop` signalled a recycled PID from emulator.pid:\n{result.stdout}"
    )


def test_stop_lets_the_emulator_go_before_tearing_the_bridge_down(
    runner: RunnerScriptHarness,
):
    """AC: the bridge must come down after the emulator's exit negotiation, not before.

    Dropping the bridge first severs the transport the stop depends on, which is how a stop
    can report success while the AVD keeps running. Both events land in one ordered call log,
    so the ordering is a fact about this run rather than a claim about the code.
    """
    runner.script(devices=[(NATIVE_SERIAL, "device")], avd_name=TARGET_AVD, boot_completed="1")
    runner.start_bridge()

    result = runner.run("stop")

    kill_at = runner.call_index("emu kill")
    bridge_down_at = runner.call_index("bridge: signal 15")
    assert kill_at >= 0, f"no `emu kill` was sent at all:\n{result.stdout}"
    assert bridge_down_at >= 0, f"the bridge was never torn down:\n{result.stdout}"
    assert kill_at < bridge_down_at, (
        "the bridge was torn down before the emulator was asked to stop:\n" + runner.calls()
    )
    assert not runner.port_is_bound(), "the bridge is still holding its port after `stop`"


# ---------------------------------------------------------------------------
# `restart`: a real stop/start cycle, not a fall-through to `start`
# ---------------------------------------------------------------------------
def test_restart_runs_the_full_stop_then_start_cycle(runner: RunnerScriptHarness):
    """AC: `restart` must execute stop *and* start.

    `restart` used to fall through the dispatcher's catch-all arm, which ran `start` against
    the already-running instance: nothing was ever stopped, so the old AVD and its bridge
    survived and the "restart" was a no-op wearing a restart's name.
    """
    state_dir = runner.install_fake_emulator()
    runner.script(devices=[], avd_name=TARGET_AVD, boot_completed="1")
    runner.start_bridge()

    result = runner.run("restart", "--daemon", env={"FAKE_EMULATOR_REGISTERS_DEVICE": "1"})

    assert result.returncode == 0, f"restart failed:\n{result.stdout}\n{result.stderr}"
    stopped_at = runner.call_index("bridge: signal 15")
    started_at = runner.call_index("emulator: launched")
    assert stopped_at >= 0, f"restart never stopped the running AVD:\n{result.output}"
    assert started_at >= 0, f"restart never launched a replacement AVD:\n{result.output}"
    assert stopped_at < started_at, (
        f"the AVD was launched before the old one was stopped:\n{runner.calls()}"
    )
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
        budget=STATUS_BUDGET_SEC,
        env={"FAKE_EMULATOR_REGISTERS_DEVICE": "1"},
    )

    assert result.returncode == 0, f"restart --daemon failed:\n{result.output}"
    assert "Attaching to live log stream" not in result.stdout, (
        f"`--daemon` was dropped and the runner attached to logs:\n{result.stdout}"
    )
    assert result.elapsed < STATUS_BUDGET_SEC, (
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

    runner.run("restart", "--daemon", env={"FAKE_EMULATOR_REGISTERS_DEVICE": "1"})

    assert runner.call_index("bridge: signal 15") >= 0, (
        "`restart` behaved like `start`: the running bridge was never stopped"
    )
