"""
tests/unit/_runner_harness.py
=============================
Shared fake-`adb` plumbing for the runner-script resilience tests (spec #241, ticket #242).

Not a test module (no `test_` prefix, so pytest never collects it): the harness copies the
real runner scripts into a throwaway runtime root and puts a scripted fake `adb` on `PATH`,
so script behaviour (wall-clock bound, exit status, stdout) can be asserted without ever
touching the shared AVD or the shared `.boss_agent/` runtime directory.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

import pytest
from _service_harness import REPO_ROOT

# Acceptance criteria: `status` completes within 5s even with an offline device present,
# and no single adb query may exceed a 3s bound.
STATUS_BUDGET_SEC = 5.0
QUERY_BOUND_CEILING_SEC = 3.0
# `emulator.sh` ships a 2s query bound and SIGKILLs 0.5s after the SIGTERM goes unanswered.
DEFAULT_QUERY_BOUND_SEC = 2.0
SIGKILL_GRACE_SEC = 0.5

# Long enough that a leaked, unkilled query is unmistakable while the suite runs.
HANG_SECONDS = 600

TARGET_AVD = "boss_avd_arm64"
BASH = shutil.which("bash") or "/bin/bash"

RUNNER_SCRIPTS = ("emulator.sh", "run.sh")

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
    # A device advertised as `offline` is the transport that wedges real adb's client, so
    # every per-device query against it blocks: that is the defect these tests model.
    if grep -qE "^${SERIAL}[[:space:]]+offline" "${SCENARIO}/devices.txt" 2>/dev/null; then
        hang_forever "${SERIAL}.offline"
    fi
    [[ -f "${SCENARIO}/hang.${SERIAL}.${COMMAND}" ]] && hang_forever "${SERIAL}.${COMMAND}"
    [[ -f "${SCENARIO}/hang.${COMMAND}" ]] && hang_forever "${COMMAND}"
    # Three-part key: hang only one subcommand, e.g. `hang.emulator-5554.emu.kill`.
    [[ -f "${SCENARIO}/hang.${SERIAL}.${COMMAND}.${1:-}" ]] \\
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


class Result(NamedTuple):
    returncode: int
    stdout: str
    stderr: str
    elapsed: float

    @property
    def output(self) -> str:
        """stdout and stderr as one stream — what a user watching the terminal sees."""
        return self.stdout + self.stderr


class RunnerScriptHarness:
    """A throwaway runtime root holding the real runner scripts and a scripted fake adb."""

    def __init__(self, tmp_path: Path) -> None:
        # Isolated runtime root: script `.boss_agent/` writes land here, never in the shared
        # worktree, and `config/` is absent so the target AVD comes from the environment.
        self.runtime_root = tmp_path / "repo"
        (self.runtime_root / ".boss_agent").mkdir(parents=True)
        for script in RUNNER_SCRIPTS:
            shutil.copy2(REPO_ROOT / script, self.runtime_root / script)

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
    ) -> Result:
        """Run the dedicated AVD runner, `emulator.sh`."""
        return self.run_script("emulator.sh", *args, budget=budget, env=env)

    def run_script(
        self,
        script: str,
        *args: str,
        budget: float = STATUS_BUDGET_SEC,
        env: dict[str, str] | None = None,
    ) -> Result:
        command_env = dict(os.environ)
        command_env["PATH"] = f"{self.bin_dir}{os.pathsep}{command_env.get('PATH', '')}"
        command_env["FAKE_ADB_SCENARIO"] = str(self.scenario)
        command_env["FAKE_ADB_PYTHON"] = sys.executable
        command_env["ANDROID_AVD"] = TARGET_AVD
        # The scripts must be exercised with their own defaults, not this machine's exports.
        for inherited in ("ADB_QUERY_TIMEOUT_SEC", "TARGET_AVD_OVERRIDE"):
            command_env.pop(inherited, None)
        command_env.update(env or {})

        started = time.monotonic()
        process = subprocess.Popen(
            [BASH, script, *args],
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
                f"`./{script} {' '.join(args)}` did not return within {budget}s — "
                "the script locked up (ticket #242)"
            )
        return Result(process.returncode, stdout, stderr, time.monotonic() - started)
