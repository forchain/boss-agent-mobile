"""
tests/unit/test_emulator_timeout_resilience.py
==============================================
`emulator.sh` resilience for the dedicated Virtual Device Session (spec #241, ticket #242).

A wedged `adb` server, a device stuck in `offline`, or an unresponsive `adbd` must never
freeze `./emulator.sh status` or serial resolution: every `adb` inspection is bounded, and
devices advertised as `offline` are skipped instead of queried.

The same suite also covers the AVD's lifecycle isolation (#363): a running emulator must
outlive the script that started it and the terminal that interrupted that script, because
the AVD is machine-wide infrastructure rather than a child of the runner.

The real `emulator.sh` is copied into a throwaway runtime root and driven against a scripted
fake `adb` on `PATH` (see `_runner_harness.py`), so these tests assert observable behaviour
— wall-clock bound, exit status, stdout — without ever touching the shared AVD, hence no
`live` marker.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import time
from pathlib import Path

import pytest
from _runner_harness import (
    DEFAULT_QUERY_BOUND_SEC,
    QUERY_BOUND_CEILING_SEC,
    SIGKILL_GRACE_SEC,
    STATUS_BUDGET_SEC,
    TARGET_AVD,
    Result,
    RunnerScriptHarness,
)

from _service_harness import REPO_ROOT, wait_until_dead_pid

pytestmark = pytest.mark.e2e

EMULATOR_SH = REPO_ROOT / "emulator.sh"

# An AVD name no real emulator can carry, for tests that reach `cmd_stop`'s `pkill` fallback.
HARNESS_ONLY_AVD = "boss_avd_harness_only"

# Long enough for the runner's `sleep 1` boot poll to still be in flight when a test
# interrupts the process group, so the AVD is launched but not yet reported as booted.
LAUNCH_BUDGET_SEC = 8.0


@pytest.fixture
def runner(tmp_path: Path) -> RunnerScriptHarness:
    return RunnerScriptHarness(tmp_path)


def _assert_returned_within_budget(result: Result) -> None:
    """AC: `status` completes within 5s even when an offline device is attached."""
    assert result.elapsed < STATUS_BUDGET_SEC, f"status took {result.elapsed:.2f}s"


# ---------------------------------------------------------------------------
# Offline / unresponsive devices must never freeze status or serial resolution
# ---------------------------------------------------------------------------
def test_offline_device_is_skipped_and_status_returns_promptly(runner: RunnerScriptHarness):
    """An attached `offline` device must not block serial resolution."""
    runner.script(devices=[("emulator-5554", "offline")], avd_name=TARGET_AVD, boot_completed="1")

    result = runner.run("status")

    _assert_returned_within_budget(result)
    assert result.returncode != 0
    assert "NOT RUNNING" in result.stdout
    # An `offline` transport cannot report its AVD name; asking it is exactly what hung the
    # script, so the device must be filtered out before any per-device query.
    assert "emu avd name" not in runner.calls(), runner.calls()


def test_wedged_adb_devices_stays_within_the_query_bound(runner: RunnerScriptHarness):
    """A stuck local adb server cannot freeze `status`: the query is bounded."""
    runner.script(devices=[("emulator-5554", "device")], hangs=["devices"])

    result = runner.run("status")

    assert result.returncode != 0
    assert "NOT RUNNING" in result.stdout
    # The fake adb never returns, so this elapsed time *is* the enforced bound.
    assert DEFAULT_QUERY_BOUND_SEC <= result.elapsed < QUERY_BOUND_CEILING_SEC, (
        f"bound was {result.elapsed:.2f}s"
    )
    assert wait_until_dead_pid(runner.hang_pid("devices"), timeout=2.0), (
        "the timed-out adb query was left running"
    )


def test_sigterm_immune_query_is_hard_killed_within_the_bound(runner: RunnerScriptHarness):
    """SIGTERM is the first resort; a wedged process that ignores it still gets killed."""
    runner.script(devices=[("emulator-5554", "device")], hangs=["devices"], stubborn=["devices"])

    result = runner.run("status")

    assert result.returncode != 0
    assert "NOT RUNNING" in result.stdout
    # Surviving the SIGTERM means the wait ran past the bound; the SIGKILL grace still lands
    # the whole query inside the 3-second contract.
    assert (
        DEFAULT_QUERY_BOUND_SEC + SIGKILL_GRACE_SEC <= result.elapsed < QUERY_BOUND_CEILING_SEC
    ), f"escalation took {result.elapsed:.2f}s"
    assert wait_until_dead_pid(runner.hang_pid("devices"), timeout=2.0), (
        "the SIGTERM-immune adb query survived the SIGKILL escalation"
    )


def test_unresponsive_online_device_does_not_block_status(runner: RunnerScriptHarness):
    """An online-looking device that never answers `emu avd name` is skipped, not waited on."""
    runner.script(devices=[("emulator-5554", "device")], hangs=["emulator-5554.emu"])

    result = runner.run("status")

    _assert_returned_within_budget(result)
    assert result.returncode != 0
    assert "NOT RUNNING" in result.stdout
    assert "emu avd name" in runner.calls(), "the device should have been queried once"


def test_serial_resolution_skips_a_hung_device_and_finds_the_healthy_one(
    runner: RunnerScriptHarness,
):
    """A hung device must not stop the scan: the next healthy device still resolves."""
    runner.script(
        devices=[("emulator-5554", "device"), ("emulator-5556", "device")],
        avd_name=TARGET_AVD,
        boot_completed="1",
        hangs=["emulator-5554.emu"],
    )

    result = runner.run("status")

    assert result.returncode == 0, f"status failed:\n{result.stdout}\n{result.stderr}"
    assert "emulator-5556" in result.stdout
    assert "ONLINE and READY" in result.stdout
    _assert_returned_within_budget(result)


def test_scan_continues_past_several_unresponsive_devices(runner: RunnerScriptHarness):
    """The healthy target behind unresponsive siblings must still be found.

    Abandoning the scan early would trade correctness for latency: `status` would report
    🔴 NOT RUNNING while the dedicated AVD is up and usable.
    """
    runner.script(
        devices=[
            ("emulator-5554", "device"),
            ("emulator-5555", "device"),
            ("emulator-5556", "device"),
        ],
        avd_name=TARGET_AVD,
        boot_completed="1",
        hangs=["emulator-5554.emu", "emulator-5555.emu"],
    )

    result = runner.run("status")

    assert result.returncode == 0, f"status failed:\n{result.stdout}\n{result.stderr}"
    assert "emulator-5556" in result.stdout
    assert "ONLINE and READY" in result.stdout
    assert runner.calls().count("emu avd name") == 3, "every candidate must be probed"
    _assert_returned_within_budget(result)


def test_stop_is_bounded_even_when_the_device_never_answers(runner: RunnerScriptHarness):
    """A wedged device must not make `stop` hang, even though it signals no device at all.

    This used to test the `emu kill` fallback: a kill the device never acknowledged, and the
    process sweep behind it. No command in this runner kills the AVD any more, so the wedge
    is now a question about `stop` alone — it still resolves a serial, still queries it, and
    must still return inside the bound rather than sitting on a query that never answers.
    """
    runner.script(
        devices=[("emulator-5554", "device")],
        avd_name=HARNESS_ONLY_AVD,
        hangs=["emulator-5554.getprop"],
    )

    result = runner.run("stop", env={"ANDROID_AVD": HARNESS_ONLY_AVD})

    _assert_returned_within_budget(result)
    assert "emu kill" not in runner.calls(), (
        f"`stop` reached for the emulator console again:\n{runner.calls()}"
    )


def test_unresponsive_boot_probe_degrades_to_booting(runner: RunnerScriptHarness):
    """A device that never answers `getprop` reads as not-yet-booted instead of hanging."""
    runner.script(
        devices=[("emulator-5554", "device")],
        avd_name=TARGET_AVD,
        hangs=["emulator-5554.shell"],
    )

    result = runner.run("status")

    _assert_returned_within_budget(result)
    assert result.returncode != 0
    assert "BOOTING" in result.stdout


def test_query_bound_knob_tightens_the_bound(runner: RunnerScriptHarness):
    """`ADB_QUERY_TIMEOUT_SEC` only ever shortens the wait (the contract caps it at 3s)."""
    runner.script(devices=[("emulator-5554", "device")], hangs=["devices"])

    result = runner.run("status", env={"ADB_QUERY_TIMEOUT_SEC": "1"})

    assert result.returncode != 0
    # A 1s bound kills the wedged query at ~1s; the default 2s bound would take ~2s.
    assert 1.0 <= result.elapsed < 1.5, f"override ignored: {result.elapsed:.2f}s"


def test_out_of_range_query_bound_is_clamped(runner: RunnerScriptHarness):
    """A nonsensical knob value falls back to the default instead of breaking the script."""
    runner.script(devices=[("emulator-5554", "device")], avd_name=TARGET_AVD, boot_completed="1")

    result = runner.run("status", env={"ADB_QUERY_TIMEOUT_SEC": "not-a-number"})

    assert result.returncode == 0, f"status failed:\n{result.stdout}\n{result.stderr}"
    assert "ONLINE and READY" in result.stdout


# ---------------------------------------------------------------------------
# Healthy devices keep working exactly as before
# ---------------------------------------------------------------------------
def test_healthy_device_still_reports_online_and_ready(runner: RunnerScriptHarness):
    runner.script(devices=[("emulator-5554", "device")], avd_name=TARGET_AVD, boot_completed="1")

    result = runner.run("status")

    assert result.returncode == 0, f"status failed:\n{result.stdout}\n{result.stderr}"
    assert "ONLINE and READY" in result.stdout
    assert "emulator-5554" in result.stdout
    _assert_returned_within_budget(result)


def test_booting_device_still_reports_booting(runner: RunnerScriptHarness):
    runner.script(devices=[("emulator-5554", "device")], avd_name=TARGET_AVD, boot_completed="")

    result = runner.run("status")

    assert result.returncode != 0
    assert "BOOTING" in result.stdout


def test_another_avd_does_not_match_the_dedicated_target(runner: RunnerScriptHarness):
    runner.script(
        devices=[("emulator-5556", "device")], avd_name="some_other_avd", boot_completed="1"
    )

    result = runner.run("status")

    assert result.returncode != 0
    assert "NOT RUNNING" in result.stdout


# ---------------------------------------------------------------------------
# Remote ADB Bridge PID lifecycle & stale detection
# ---------------------------------------------------------------------------
def test_bridge_status_ignores_stale_pid_of_unrelated_process(runner: RunnerScriptHarness):
    """When remote_bridge.pid points to an unrelated live process, status reports NOT RUNNING
    and cleans up the stale PID file instead of falsely reporting the bridge is listening."""
    import os

    runner.script(devices=[("emulator-5554", "device")], avd_name=TARGET_AVD, boot_completed="1")
    dot_boss = runner.runtime_root / ".boss_agent"
    dot_boss.mkdir(parents=True, exist_ok=True)
    bridge_pid_file = dot_boss / "remote_bridge.pid"
    # Write current test process PID (definitely alive, definitely not remote_adb_bridge)
    bridge_pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")

    result = runner.run("status")

    assert result.returncode == 0
    assert "Remote ADB Bridge is NOT RUNNING" in result.stdout
    assert "Remote ADB Bridge is LISTENING" not in result.stdout
    assert not bridge_pid_file.exists(), "stale pidfile must be cleaned up"


# ---------------------------------------------------------------------------
# AVD lifecycle isolation (#363)
# ---------------------------------------------------------------------------
# The dedicated AVD is machine-wide infrastructure: it is shared by every worktree, and
# `./emulator.sh` is usually left attached to its log stream in a foreground terminal.
# Tearing the AVD down because the user pressed Ctrl+C to stop watching the log would be
# wrong, so the runner has to start it outside the terminal's process group.


@pytest.fixture
def launched_avd(runner: RunnerScriptHarness):
    """Yield `(runner, state_dir, script_process)` for a just-launched, still-booting AVD.

    The fake emulator is installed on PATH and the fake adb reports no device yet, so
    `emulator.sh start` launches the AVD and then waits in its boot poll. That window is
    where a test can act on the runner while the AVD is already running.
    """
    state_dir = runner.install_fake_emulator()
    runner.script(devices=[])
    process = runner.spawn_script("emulator.sh", "start")
    deadline = time.monotonic() + LAUNCH_BUDGET_SEC
    # Wait for the *complete* identity report, not just the pid file: the fake emulator
    # writes `pid` first and only then spawns the helper that fills `session`, so a bare
    # existence check races that second write and the session read below raises IndexError
    # roughly half the time under load.
    while time.monotonic() < deadline:
        session = state_dir / "session"
        if (
            (state_dir / "pid").exists()
            and session.exists()
            and len(session.read_text(encoding="utf-8").split()) == 3
        ):
            break
        assert process.poll() is None, "emulator.sh exited before launching the AVD"
        time.sleep(0.05)
    assert (state_dir / "pid").exists(), "the fake emulator was never launched"
    assert len((state_dir / "session").read_text(encoding="utf-8").split()) == 3, (
        "the fake emulator never reported its session and process group"
    )
    try:
        yield runner, state_dir, process
    finally:
        if process.poll() is None:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            process.wait(timeout=5)
        # The fake emulator sits in a session of its own, so nothing else reaps it.
        emu_pid = runner.emulator_pid(state_dir)
        if not wait_until_dead_pid(emu_pid, timeout=0.5):
            with contextlib.suppress(OSError):
                os.kill(emu_pid, signal.SIGKILL)


def test_avd_is_launched_in_its_own_session(launched_avd):
    """The AVD must not share a session or process group with the runner that started it.

    This is the property the whole isolation rests on. A process in the runner's own group
    is reachable by anything that cleans up that group, and a process in the runner's own
    session still holds the terminal as its controlling terminal. `sid == pid == pgid` is
    what "isolated" looks like.
    """
    runner, state_dir, process = launched_avd

    emu_pid = runner.emulator_pid(state_dir)
    emu_sid, emu_pgid = runner.emulator_session(state_dir)

    assert emu_sid == emu_pid, (
        f"the emulator's session id ({emu_sid}) must be its own pid ({emu_pid}); sharing the "
        "runner's session leaves it attached to the terminal"
    )
    assert emu_pgid == emu_pid, (
        f"the emulator's process group ({emu_pgid}) must be its own ({emu_pid}); sharing the "
        "runner's group makes a group-wide cleanup reach the AVD"
    )
    assert emu_pgid != os.getpgid(process.pid), (
        "the emulator ended up in the runner's process group, so anything that cleans that "
        "group up would take the AVD down with it"
    )


def test_avd_survives_a_hard_kill_of_the_runner_process_group(launched_avd):
    """A SIGKILL sweep of the runner's group must leave the AVD untouched.

    This is the discriminating test for the isolation. POSIX already makes a background job
    ignore SIGINT and SIGQUIT, so Ctrl+C alone cannot show whether the AVD was isolated;
    SIGKILL can be neither trapped nor inherited as ignored, so a surviving AVD here can
    only mean it is genuinely outside the group. Supervisors, CI teardown and the test
    harnesses all clean up by sweeping a process group exactly this way.
    """
    runner, state_dir, process = launched_avd
    emu_pid = runner.emulator_pid(state_dir)

    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    process.wait(timeout=5)

    assert not wait_until_dead_pid(emu_pid, timeout=0.5), (
        "the AVD was swept away with the runner's process group; it is machine-wide "
        "infrastructure shared by every worktree, not a child of the runner"
    )


def test_running_avd_survives_a_terminal_interrupt(launched_avd):
    """Ctrl+C in the terminal must not kill the running AVD.

    The runner's own exit is the positive control proving the group signal was really
    delivered, so this cannot pass by the signal simply never arriving. Note this case is
    already covered by POSIX — a background job ignores SIGINT — which is exactly why the
    isolation needs a test that POSIX cannot excuse.
    """
    runner, state_dir, process = launched_avd
    emu_pid = runner.emulator_pid(state_dir)

    os.killpg(os.getpgid(process.pid), signal.SIGINT)

    deadline = time.monotonic() + 3.0
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert process.poll() is not None, "the runner ignored the group SIGINT; test is vacuous"

    assert not wait_until_dead_pid(emu_pid, timeout=0.5), (
        "the AVD was killed by the terminal interrupt; it must outlive the runner"
    )
    assert runner.emulator_received_signal(state_dir) == "", (
        f"the AVD received {runner.emulator_received_signal(state_dir)!r} from the terminal "
        "interrupt and is only surviving by luck"
    )


def test_running_avd_survives_the_runner_exiting(launched_avd):
    """A clean `emulator.sh` exit must leave the AVD running.

    This is the ordinary path: `./emulator.sh start --daemon` returns once the device is
    up, and the AVD has to keep running for every other worktree.
    """
    runner, state_dir, process = launched_avd
    emu_pid = runner.emulator_pid(state_dir)

    # Let the boot poll time out so the runner exits on its own, then confirm the AVD lives.
    deadline = time.monotonic() + LAUNCH_BUDGET_SEC
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)

    assert not wait_until_dead_pid(emu_pid, timeout=0.5), (
        "the AVD died with the runner; it is machine-wide infrastructure, not a child"
    )


def test_pidfile_records_the_emulator_itself(launched_avd):
    """The recorded PID must be the emulator, not a wrapper that could exit first.

    A wrapper PID would go stale the moment it exec'd away, and `emulator.sh stop`'s
    `pkill` fallback is what a stale pidfile would end up missing.
    """
    runner, state_dir, _process = launched_avd

    recorded = (
        (runner.runtime_root / ".boss_agent" / "emulator.pid").read_text(encoding="utf-8").strip()
    )

    assert recorded == str(runner.emulator_pid(state_dir)), (
        "emulator.pid must name the emulator process itself so `stop` can still reach it "
        "after the runner has exited"
    )


# ---------------------------------------------------------------------------
# Structural guard: no adb call site may escape the bound
# ---------------------------------------------------------------------------
_ADB_REFERENCE = re.compile(r"\$\{ADB_BIN\}|\$ADB_BIN")
_PRESENCE_GUARD = re.compile(r'if \[\[ -z "\$\{ADB_BIN\}" \]\]; then')


def test_every_emulator_sh_adb_invocation_goes_through_the_bounded_runner():
    """Every adb call site must be wrapped; a bare `${ADB_BIN}` call would be unbounded."""
    script = EMULATOR_SH.read_text(encoding="utf-8")
    offenders: list[str] = []
    references = 0
    wrapped = 0
    for number, line in enumerate(script.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#") or not _ADB_REFERENCE.search(line):
            continue
        references += 1
        if "bounded_run" in line:
            wrapped += 1
            continue
        if len(_ADB_REFERENCE.findall(line)) == 1 and _PRESENCE_GUARD.fullmatch(stripped):
            continue
        offenders.append(f"  emulator.sh:{number}: {stripped}")
    assert not offenders, "unbounded adb invocation(s):\n" + "\n".join(offenders)
    # Positive controls: without them this test would keep passing if the guard's patterns
    # stopped matching the script at all.
    assert "bounded_run() {" in script, "the bounded runner definition is gone"
    assert references >= 3 and wrapped >= 1, (
        f"the guard matched only {references} adb reference(s) ({wrapped} wrapped) — "
        "it has gone stale and no longer protects anything"
    )


def test_bounded_run_does_not_leak_temporary_files_on_timeout_or_failure(
    runner: RunnerScriptHarness,
):
    """Temporary files generated by bounded queries must be cleaned up in the runner runtime directory."""
    runner.script(devices=[("emulator-5554", "device")], hangs=["devices"])

    result = runner.run("status")

    assert result.returncode != 0
    # The dedicated runner runtime directory (.boss_agent/run) must exist and contain no leftover temp files
    runner_tmp_dir = runner.runtime_root / ".boss_agent" / "run"
    assert runner_tmp_dir.is_dir(), f"expected runner temp directory at {runner_tmp_dir}"
    leftover = list(runner_tmp_dir.glob("*boss_agent_bounded*"))
    assert not leftover, f"temporary file(s) leaked after query timeout: {leftover}"
