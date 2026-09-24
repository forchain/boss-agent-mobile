"""
tests/unit/test_emulator_timeout_resilience.py
==============================================
`emulator.sh` resilience for the dedicated Virtual Device Session (spec #241, ticket #242).

A wedged `adb` server, a device stuck in `offline`, or an unresponsive `adbd` must never
freeze `./emulator.sh status` or serial resolution: every `adb` inspection is bounded, and
devices advertised as `offline` are skipped instead of queried.

The real `emulator.sh` is copied into a throwaway runtime root and driven against a
scripted fake `adb` on `PATH`, so these tests assert observable behaviour (wall-clock
bound, exit status, stdout) and never touch the shared AVD — hence no `live` marker.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

import pytest
from _service_harness import REPO_ROOT, wait_until_dead_pid

EMULATOR_SH = REPO_ROOT / "emulator.sh"
BASH = shutil.which("bash") or "/bin/bash"
TARGET_AVD = "boss_avd_arm64"

# Acceptance criteria: `status` completes within 5s even with an offline device present,
# and no single adb query may exceed a 3s bound.
STATUS_BUDGET_SEC = 5.0
QUERY_BOUND_CEILING_SEC = 3.0
# `emulator.sh` ships a 2s query bound and SIGKILLs 0.5s after the SIGTERM goes unanswered.
DEFAULT_QUERY_BOUND_SEC = 2.0
SIGKILL_GRACE_SEC = 0.5

# Long enough that a leaked, unkilled query is unmistakable while the suite runs.
HANG_SECONDS = 600

_FAKE_ADB = """#!/usr/bin/env bash
# Scripted `adb` stand-in: reads its answers (and its hang behaviour) from
# ${FAKE_ADB_SCENARIO} and records every call in calls.log.
set -u
SCENARIO="${FAKE_ADB_SCENARIO}"

log_call() { printf '%s\\n' "$*" >> "${SCENARIO}/calls.log"; }

hang_forever() {
    printf '%s\\n' "$$" > "${SCENARIO}/$1.hangpid"
    # A `stubborn` call is a real SIGTERM-immune process, so only the SIGKILL escalation can
    # stop it. `trap '' TERM` would not do: a bash trap set in a function does not survive
    # the `exec` that follows.
    if [[ -f "${SCENARIO}/stubborn.$1" ]]; then
        exec "${FAKE_ADB_PYTHON}" -c \\
            'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)'
    fi
    exec sleep __HANG_SECONDS__
}

if [[ "${1:-}" == "devices" ]]; then
    log_call "devices"
    [[ -f "${SCENARIO}/hang.devices" ]] && hang_forever devices
    cat "${SCENARIO}/devices.txt" 2>/dev/null || true
    exit 0
fi

if [[ "${1:-}" == "-s" ]]; then
    SERIAL="${2:-}"
    shift 2 || true
    COMMAND="${1:-}"
    shift || true
    log_call "${SERIAL} ${COMMAND} $*"
    [[ -f "${SCENARIO}/hang.${SERIAL}.${COMMAND}" ]] && hang_forever "${SERIAL}.${COMMAND}"
    [[ -f "${SCENARIO}/hang.${COMMAND}" ]] && hang_forever "${COMMAND}"
    # Three-part key: hang only one subcommand, e.g. `hang.emulator-5554.emu.kill`.
    [[ -f "${SCENARIO}/hang.${SERIAL}.${COMMAND}.${1:-}" ]] \
        && hang_forever "${SERIAL}.${COMMAND}.${1:-}"

    case "${COMMAND} ${1:-}" in
        "emu avd") cat "${SCENARIO}/avd_name.txt" 2>/dev/null || true ;;
        "emu kill") echo "OK: killed" ;;
        "shell getprop") cat "${SCENARIO}/boot_completed.txt" 2>/dev/null || true ;;
    esac
    exit 0
fi

exit 1
"""


class _Result(NamedTuple):
    returncode: int
    stdout: str
    stderr: str
    elapsed: float


class _AvdRunner:
    """A throwaway runtime root holding a copy of the real `emulator.sh` and a fake `adb`."""

    def __init__(self, tmp_path: Path) -> None:
        # Isolated runtime root: the script's `.boss_agent/` writes land here, never in the
        # shared worktree, and `config/` is absent so the target AVD comes from the env.
        self.runtime_root = tmp_path / "repo"
        (self.runtime_root / ".boss_agent").mkdir(parents=True)
        shutil.copy2(EMULATOR_SH, self.runtime_root / "emulator.sh")

        self.scenario = tmp_path / "scenario"
        self.scenario.mkdir()
        self.bin_dir = tmp_path / "fakebin"
        self.bin_dir.mkdir()
        fake_adb = self.bin_dir / "adb"
        fake_adb.write_text(
            _FAKE_ADB.replace("__HANG_SECONDS__", str(HANG_SECONDS)), encoding="utf-8"
        )
        fake_adb.chmod(0o755)

    # --- scenario scripting -------------------------------------------------
    def script(
        self,
        devices: Sequence[tuple[str, str]] = (),
        *,
        avd_name: str | None = None,
        boot_completed: str | None = None,
        hangs: Sequence[str] = (),
        stubborn: Sequence[str] = (),
    ) -> None:
        """Describe what the fake `adb` reports, and which calls never answer."""
        device_lines = "".join(f"{serial}\t{state}\n" for serial, state in devices)
        (self.scenario / "devices.txt").write_text(
            f"List of devices attached\n{device_lines}\n", encoding="utf-8"
        )
        if avd_name is not None:
            (self.scenario / "avd_name.txt").write_text(f"{avd_name}\n", encoding="utf-8")
        if boot_completed is not None:
            (self.scenario / "boot_completed.txt").write_text(
                f"{boot_completed}\n", encoding="utf-8"
            )
        for hang in hangs:
            (self.scenario / f"hang.{hang}").write_text("", encoding="utf-8")
        for stubborn_call in stubborn:
            (self.scenario / f"stubborn.{stubborn_call}").write_text("", encoding="utf-8")

    def calls(self) -> str:
        calls_log = self.scenario / "calls.log"
        return calls_log.read_text(encoding="utf-8") if calls_log.exists() else ""

    def hang_pid(self, key: str) -> int:
        """PID of the still-running fake adb call recorded under `key`."""
        return int((self.scenario / f"{key}.hangpid").read_text(encoding="utf-8").strip())

    # --- execution ----------------------------------------------------------
    def run(
        self,
        *args: str,
        budget: float = STATUS_BUDGET_SEC,
        env: dict[str, str] | None = None,
    ) -> _Result:
        command_env = dict(os.environ)
        command_env["PATH"] = f"{self.bin_dir}{os.pathsep}{command_env.get('PATH', '')}"
        command_env["FAKE_ADB_SCENARIO"] = str(self.scenario)
        command_env["FAKE_ADB_PYTHON"] = sys.executable
        command_env["ANDROID_AVD"] = TARGET_AVD
        # The script must be exercised with its own defaults, not this machine's exports.
        for inherited in ("ADB_QUERY_TIMEOUT_SEC", "TARGET_AVD_OVERRIDE"):
            command_env.pop(inherited, None)
        command_env.update(env or {})

        started = time.monotonic()
        process = subprocess.Popen(
            [BASH, "emulator.sh", *args],
            cwd=str(self.runtime_root),
            env=command_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=budget)
        except subprocess.TimeoutExpired:
            # Never leak a locked-up script (or its hanging adb) into the rest of the suite.
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            process.communicate()
            pytest.fail(
                f"`./emulator.sh {' '.join(args)}` did not return within {budget}s — "
                "the script locked up (ticket #242)"
            )
        return _Result(process.returncode, stdout, stderr, time.monotonic() - started)


@pytest.fixture
def runner(tmp_path: Path) -> _AvdRunner:
    return _AvdRunner(tmp_path)


def _assert_returned_within_budget(result: _Result) -> None:
    """AC: `status` completes within 5s even when an offline device is attached."""
    assert result.elapsed < STATUS_BUDGET_SEC, f"status took {result.elapsed:.2f}s"


# ---------------------------------------------------------------------------
# Offline / unresponsive devices must never freeze status or serial resolution
# ---------------------------------------------------------------------------
def test_offline_device_is_skipped_and_status_returns_promptly(runner: _AvdRunner):
    """An attached `offline` device must not block serial resolution."""
    runner.script(devices=[("emulator-5554", "offline")], avd_name=TARGET_AVD, boot_completed="1")

    result = runner.run("status")

    _assert_returned_within_budget(result)
    assert result.returncode != 0
    assert "NOT RUNNING" in result.stdout
    # An `offline` transport cannot report its AVD name; asking it is exactly what hung the
    # script, so the device must be filtered out before any per-device query.
    assert "emu avd name" not in runner.calls(), runner.calls()


def test_wedged_adb_devices_stays_within_the_query_bound(runner: _AvdRunner):
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


def test_sigterm_immune_query_is_hard_killed_within_the_bound(runner: _AvdRunner):
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


def test_unresponsive_online_device_does_not_block_status(runner: _AvdRunner):
    """An online-looking device that never answers `emu avd name` is skipped, not waited on."""
    runner.script(devices=[("emulator-5554", "device")], hangs=["emulator-5554.emu"])

    result = runner.run("status")

    _assert_returned_within_budget(result)
    assert result.returncode != 0
    assert "NOT RUNNING" in result.stdout
    assert "emu avd name" in runner.calls(), "the device should have been queried once"


def test_serial_resolution_skips_a_hung_device_and_finds_the_healthy_one(runner: _AvdRunner):
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


def test_scan_continues_past_several_unresponsive_devices(runner: _AvdRunner):
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


def test_stop_does_not_claim_success_when_the_kill_goes_unanswered(runner: _AvdRunner):
    """`emu kill` is bounded too, and a bounded-away kill must not report a clean stop."""
    runner.script(
        devices=[("emulator-5554", "device")],
        avd_name=TARGET_AVD,
        hangs=["emulator-5554.emu.kill"],
    )

    result = runner.run("stop")

    _assert_returned_within_budget(result)
    assert "did not acknowledge" in result.stdout, result.stdout
    assert "✅" not in result.stdout, f"stop reported success it never confirmed:\n{result.stdout}"


def test_unresponsive_boot_probe_degrades_to_booting(runner: _AvdRunner):
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


def test_query_bound_knob_tightens_the_bound(runner: _AvdRunner):
    """`ADB_QUERY_TIMEOUT_SEC` only ever shortens the wait (the contract caps it at 3s)."""
    runner.script(devices=[("emulator-5554", "device")], hangs=["devices"])

    result = runner.run("status", env={"ADB_QUERY_TIMEOUT_SEC": "1"})

    assert result.returncode != 0
    # A 1s bound kills the wedged query at ~1s; the default 2s bound would take ~2s.
    assert 1.0 <= result.elapsed < 1.5, f"override ignored: {result.elapsed:.2f}s"


def test_out_of_range_query_bound_is_clamped(runner: _AvdRunner):
    """A nonsensical knob value falls back to the default instead of breaking the script."""
    runner.script(devices=[("emulator-5554", "device")], avd_name=TARGET_AVD, boot_completed="1")

    result = runner.run("status", env={"ADB_QUERY_TIMEOUT_SEC": "not-a-number"})

    assert result.returncode == 0, f"status failed:\n{result.stdout}\n{result.stderr}"
    assert "ONLINE and READY" in result.stdout


# ---------------------------------------------------------------------------
# Healthy devices keep working exactly as before
# ---------------------------------------------------------------------------
def test_healthy_device_still_reports_online_and_ready(runner: _AvdRunner):
    runner.script(devices=[("emulator-5554", "device")], avd_name=TARGET_AVD, boot_completed="1")

    result = runner.run("status")

    assert result.returncode == 0, f"status failed:\n{result.stdout}\n{result.stderr}"
    assert "ONLINE and READY" in result.stdout
    assert "emulator-5554" in result.stdout
    _assert_returned_within_budget(result)


def test_booting_device_still_reports_booting(runner: _AvdRunner):
    runner.script(devices=[("emulator-5554", "device")], avd_name=TARGET_AVD, boot_completed="")

    result = runner.run("status")

    assert result.returncode != 0
    assert "BOOTING" in result.stdout


def test_another_avd_does_not_match_the_dedicated_target(runner: _AvdRunner):
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
