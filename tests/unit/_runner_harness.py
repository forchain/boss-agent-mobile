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

RUNNER_SCRIPTS = ("emulator.sh", "run.sh", "worker.sh", "dashboard.sh")
#: Sourced by every runner, so a copied script must find it beside itself.
RUNNER_LIBRARY = "runner_lib.sh"

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

if [[ "${1:-}" == "connect" || "${1:-}" == "disconnect" ]]; then
    log_call "$*"
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

# A fake `emulator` for the launch-path tests. A real AVD process runs until it is killed,
# so this one records who launched it, which session and process group it ended up in, and
# every signal it receives. Those facts are what the isolation tests assert on: a process in
# the runner's own process group is reachable from a terminal Ctrl+C, and one in a session of
# its own is not.
_FAKE_EMULATOR = """#!/usr/bin/env bash
# Fake `emulator`: reports its own process identity, then stays alive like a real AVD.
set -u
STATE="${FAKE_ADB_SCENARIO}/emulator"

printf '%s\\n' "$$" > "${STATE}/pid"
# Sessions and process groups are inherited across fork/exec, so a child reporting its own
# ids reports this script's too. Asked of Python rather than `ps` because macOS `ps` has no
# `sid` keyword and silently answers with the wrong columns. The first field is the
# reporting child's own pid and is ignored; only the two ids are read back.
"${FAKE_ADB_PYTHON}" -c 'import os; print(os.getpid(), os.getsid(0), os.getpgid(0))' \\
    > "${STATE}/session"
printf 'fake emulator launched: %s\\n' "$*" >> "${STATE}/log"

# Record the signal, then stay alive. The suite asserts on the absence of these lines, so
# a regression that lets the terminal signal through cannot pass silently.
trap 'printf "INT\\n" >> "${STATE}/signals"' INT
trap 'printf "TERM\\n" >> "${STATE}/signals"' TERM
trap 'printf "HUP\\n" >> "${STATE}/signals"' HUP

while true; do
    sleep 0.2
done
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
        for script in (*RUNNER_SCRIPTS, RUNNER_LIBRARY):
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

    # --- fake emulator --------------------------------------------------------
    def install_fake_emulator(self) -> Path:
        """Put a fake `emulator` on PATH and return the state directory it reports into.

        `emulator.sh` resolves the binary through `command -v emulator`, so a stand-in on
        PATH is enough to drive the real launch path — including the session isolation
        that keeps a running AVD alive after the runner exits (#363).
        """
        state_dir = self.scenario / "emulator"
        state_dir.mkdir(exist_ok=True)
        fake_emulator = self.bin_dir / "emulator"
        fake_emulator.write_text(_FAKE_EMULATOR, encoding="utf-8")
        fake_emulator.chmod(0o755)
        return state_dir

    def emulator_pid(self, state_dir: Path) -> int:
        """PID the fake emulator recorded for itself when it was launched."""
        return int((state_dir / "pid").read_text(encoding="utf-8").strip())

    def emulator_session(self, state_dir: Path) -> tuple[int, int]:
        """`(sid, pgid)` of the launched emulator.

        The signal isolation under test is exactly "is this process reachable from the
        runner's terminal group", so the session and group ids are the evidence. Compare
        them against `emulator_pid`, which is the emulator's own PID.
        """
        fields = (state_dir / "session").read_text(encoding="utf-8").split()
        return int(fields[1]), int(fields[2])

    def emulator_received_signal(self, state_dir: Path) -> str:
        """The signal the fake emulator observed, or empty when it was left alone."""
        signal_log = state_dir / "signals"
        return signal_log.read_text(encoding="utf-8").strip() if signal_log.exists() else ""

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
    def command_env(self, env: dict[str, str] | None = None) -> dict[str, str]:
        command_env = dict(os.environ)
        command_env["PATH"] = f"{self.bin_dir}{os.pathsep}{command_env.get('PATH', '')}"
        command_env["FAKE_ADB_SCENARIO"] = str(self.scenario)
        command_env["FAKE_ADB_PYTHON"] = sys.executable
        command_env["ANDROID_AVD"] = TARGET_AVD
        # The scripts must be exercised with their own defaults, not this machine's exports.
        for inherited in ("ADB_QUERY_TIMEOUT_SEC", "TARGET_AVD_OVERRIDE"):
            command_env.pop(inherited, None)
        command_env.update(env or {})
        return command_env

    def spawn_script(self, script: str, *args: str, env: dict[str, str] | None = None):
        """Start a runner script without waiting for it, so a test can act while it runs.

        The script is placed in a session of its own (`start_new_session=True`), which
        makes its PID also its process-group id. Signalling that group is therefore exactly
        what a terminal Ctrl+C does to the script's foreground group — the situation the
        AVD isolation has to survive (#363).
        """
        return subprocess.Popen(
            [BASH, script, *args],
            cwd=str(self.runtime_root),
            env=self.command_env(env),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

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
        command_env = self.command_env(env)

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
