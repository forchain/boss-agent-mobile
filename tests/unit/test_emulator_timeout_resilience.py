"""
tests/unit/test_emulator_timeout_resilience.py
==============================================
`emulator.sh` resilience for the dedicated Virtual Device Session (spec #241, ticket #242).

A wedged `adb` server, a device stuck in `offline`, or an unresponsive `adbd` must never
freeze `./emulator.sh status` or serial resolution: every `adb` inspection is bounded, and
devices advertised as `offline` are skipped instead of queried.

The real `emulator.sh` is copied into a throwaway runtime root and driven against a scripted
fake `adb` on `PATH` (see `_runner_harness.py`), so these tests assert observable behaviour
— wall-clock bound, exit status, stdout — without ever touching the shared AVD, hence no
`live` marker.
"""

from __future__ import annotations

import re
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

EMULATOR_SH = REPO_ROOT / "emulator.sh"

# An AVD name no real emulator can carry, for tests that reach `cmd_stop`'s `pkill` fallback.
HARNESS_ONLY_AVD = "boss_avd_harness_only"


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


def test_stop_does_not_claim_success_when_the_kill_goes_unanswered(runner: RunnerScriptHarness):
    """`emu kill` is bounded too, and a bounded-away kill must not report a clean stop."""
    # `cmd_stop` falls back to `pkill -f "emulator.*@<avd>"`, so this test must not name a
    # real AVD: a live emulator for the dedicated target must never be signalled by the suite.
    runner.script(
        devices=[("emulator-5554", "device")],
        avd_name=HARNESS_ONLY_AVD,
        hangs=["emulator-5554.emu.kill"],
    )

    result = runner.run("stop", env={"ANDROID_AVD": HARNESS_ONLY_AVD})

    _assert_returned_within_budget(result)
    assert "did not acknowledge" in result.stdout, result.stdout
    assert "Stopped emulator processes" in result.stdout, "the fallback cleanup did not run"
    assert "✅" not in result.stdout, f"stop reported success it never confirmed:\n{result.stdout}"


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
