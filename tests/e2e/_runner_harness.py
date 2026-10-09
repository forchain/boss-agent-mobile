"""
tests/unit/_runner_harness.py
=============================
Shared fake-`adb` plumbing for the runner-script resilience tests (spec #241, ticket #242).

Not a test module (no `test_` prefix, so pytest never collects it): the harness copies the
real runner scripts into a throwaway runtime root and puts a scripted fake `adb` on `PATH`,
so script behaviour (wall-clock bound, exit status, stdout) can be asserted without ever
touching the shared AVD or the shared `.boss_agent/` runtime directory.

That claim is only true because of `TARGET_AVD` and `REMOTE_ADB_PORT` below, and both are
load-bearing in a way that is easy to miss. The fake `adb` makes a fake *transport*; it
cannot make a fake *process*, and a port number is not a sandbox. `emulator.sh` locates its
AVD with a machine-wide `pgrep -f` over the AVD name and finds its bridge by asking who is
listening on `REMOTE_ADB_PORT`. Both read the developer's real machine, so the only things
separating the suite from it are the *name* and the *port* it hands the runner:

* `TARGET_AVD` must be a name no machine can have, or a test that forgets to override it
  signals the developer's live emulator. This cost a real AVD once.
* `REMOTE_ADB_PORT` must be a free port, or a test's `stop` resolves the port's real owner
  and signals the developer's own bridge. This cost a real bridge once.

Neither is caught by the fakes, so both are asserted in the suite rather than trusted:
`test_the_harness_avd_name_cannot_be_a_real_avd`, and the free-port reservation below.

Two properties of the `adb` fake are load-bearing rather than decorative, so keep them
faithful:

* `adb connect <serial>` really adds a transport to the device list, and `adb disconnect`
  really removes it. Serial priority, LAN-connect health and `reconnect` self-healing are
  all questions about *which transports adb reports*, and a fake that merely logs `connect`
  would leave every one of those questions untestable.
* A per-serial AVD-name override exists. A LAN bridge endpoint forwards to the *same* adbd
  as the local emulator, so it legitimately reports the same AVD name; with one global name
  the fake could never reproduce "both transports match, prefer the native one".
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

import pytest

from _service_harness import REPO_ROOT, free_port

# Acceptance criteria: `status` completes within 5s even with an offline device present,
# and no single adb query may exceed a 3s bound.
STATUS_BUDGET_SEC = 5.0
QUERY_BOUND_CEILING_SEC = 3.0
# `emulator.sh` ships a 2s query bound and SIGKILLs 0.5s after the SIGTERM goes unanswered.
DEFAULT_QUERY_BOUND_SEC = 2.0
SIGKILL_GRACE_SEC = 0.5

# Long enough that a leaked, unkilled query is unmistakable while the suite runs.
HANG_SECONDS = 600

#: The AVD name every runner script under test is pointed at.
#:
#: This must be a name that cannot exist on a developer's machine, and that is the whole
#: reason it is not simply the project's real AVD. `emulator.sh` finds its AVD's process with
#: a machine-wide `pgrep -f` over the AVD name, so a test that inherits this constant points
#: that scan at whatever the developer actually has running — and on the old value, which was
#: the real `boss_avd_arm64`, the suite killed the developer's live emulator and left its LAN
#: bridge transport offline. Tests that spawn their own AVD process pass
#: `ANDROID_AVD=HARNESS_AVD` explicitly for the same reason; this constant is the belt to
#: that braces, and `test_the_harness_avd_name_cannot_be_a_real_avd` checks it.
TARGET_AVD = "boss_avd_e2e_fixture"
BASH = shutil.which("bash") or "/bin/bash"

# A fixed RFC 1918 address, so every LAN-dependent line of output is deterministic and never
# names the developer's own network.
FAKE_LAN_IP = "192.168.77.42"
#: The serial a launched AVD registers under when a test asks it to.
FAKE_EMULATOR_SERIAL = "emulator-5554"

RUNNER_SCRIPTS = ("emulator.sh", "run.sh", "worker.sh", "dashboard.sh")
#: Sourced by every runner, so a copied script must find it beside itself.
RUNNER_LIBRARY = "runner_lib.sh"

_FAKE_ADB = r"""#!/usr/bin/env bash
# Scripted `adb` stand-in: reads its answers (and its hang behaviour) from
# ${FAKE_ADB_SCENARIO} and records every call in calls.log.
set -u
SCENARIO="${FAKE_ADB_SCENARIO}"

log_call() { printf '%s\n' "$*" >> "${SCENARIO}/calls.log"; }

# A per-serial override first, then the answer shared by every other transport.
answer() {
    local KEY="$1" FALLBACK="$2" SERIAL="$3"
    if [[ -f "${SCENARIO}/${KEY}.${SERIAL}" ]]; then
        cat "${SCENARIO}/${KEY}.${SERIAL}"
    else
        cat "${SCENARIO}/${FALLBACK}" 2>/dev/null || true
    fi
}

hang_forever() {
    printf '%s\n' "$$" > "${SCENARIO}/$1.hangpid"
    # A `stubborn` call is a real SIGTERM-immune process, so only the SIGKILL escalation can
    # stop it. `trap '' TERM` would not do: a bash trap set in a function does not survive
    # the `exec` that follows.
    if [[ -f "${SCENARIO}/stubborn.$1" ]]; then
        exec "${FAKE_ADB_PYTHON}" -c \
            'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)'
    fi
    exec sleep __HANG_SECONDS__
}

if [[ "${1:-}" == "devices" ]]; then
    log_call "devices"
    [[ -f "${SCENARIO}/hang.devices" ]] && hang_forever devices
    cat "${SCENARIO}/devices.txt" 2>/dev/null || true
    # A transport established by `adb connect` appears in the device list, as on a real host.
    if [[ -f "${SCENARIO}/connected.txt" ]]; then
        while read -r serial; do
            [[ -n "${serial}" ]] || continue
            printf '%s\tdevice\n' "${serial}"
        done < "${SCENARIO}/connected.txt"
    fi
    exit 0
fi

if [[ "${1:-}" == "connect" || "${1:-}" == "disconnect" ]]; then
    log_call "$*"
    if [[ "${1:-}" == "connect" ]]; then
        printf '%s\n' "${2:-}" >> "${SCENARIO}/connected.txt"
        echo "connected to ${2:-}"
    else
        if [[ -f "${SCENARIO}/connected.txt" ]]; then
            grep -vxF "${2:-}" "${SCENARIO}/connected.txt" > "${SCENARIO}/connected.tmp" || true
            mv "${SCENARIO}/connected.tmp" "${SCENARIO}/connected.txt"
        fi
        echo "disconnected ${2:-}"
    fi
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
        "emu avd") answer avd_name avd_name.txt "${SERIAL}" ;;
        "emu kill") echo "OK: killed" ;;
        "shell getprop")
            # A non-emulator transport has no emulator console, so AVD-name resolution falls
            # back to this property — which is exactly how a LAN bridge endpoint reports the
            # AVD it forwards to.
            if [[ "${2:-}" == "ro.boot.qemu.avd_name" ]]; then
                answer avd_name avd_name.txt "${SERIAL}"
            else
                answer boot_completed boot_completed.txt "${SERIAL}"
            fi
            ;;
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
_FAKE_EMULATOR = r"""#!/usr/bin/env bash
# Fake `emulator`: reports its own process identity, then stays alive like a real AVD.
set -u
SCENARIO="${FAKE_ADB_SCENARIO}"
STATE="${SCENARIO}/emulator"
mkdir -p "${STATE}"

log_call() { printf '%s\n' "$*" >> "${SCENARIO}/calls.log"; }

if [[ "${1:-}" == "-list-avds" ]]; then
    printf '%s\n' "${FAKE_AVD_NAME}"
    printf '%s\n' "some_other_avd"
    exit 0
fi

# Logged to the shared call log before anything else, so a test can assert the *ordering* of
# a launch against the bridge teardown the script performs around it.
log_call "emulator: launched $*"

printf '%s\n' "$$" > "${STATE}/pid"
# Sessions and process groups are inherited across fork/exec, so a child reporting its own
# ids reports this script's too. Asked of Python rather than `ps` because macOS `ps` has no
# `sid` keyword and silently answers with the wrong columns. The first field is the
# reporting child's own pid and is ignored; only the two ids are read back.
"${FAKE_ADB_PYTHON}" -c 'import os; print(os.getpid(), os.getsid(0), os.getpgid(0))' \
    > "${STATE}/session"
printf 'fake emulator launched: %s\n' "$*" >> "${STATE}/log"

# Registering the launched AVD in `adb devices` is opt-in. The launch-isolation tests need
# `cmd_start`'s boot poll to time out with the AVD alive but absent from the device list,
# while a test driving a whole start/stop cycle needs the opposite; the two cannot both be
# the default.
if [[ "${FAKE_EMULATOR_REGISTERS_DEVICE:-0}" == "1" ]]; then
    SERIAL="${FAKE_EMULATOR_SERIAL}"
    grep -qE "^${SERIAL}[[:space:]]" "${FAKE_ADB_SCENARIO}/devices.txt" 2>/dev/null \
        || printf '%s\tdevice\n' "${SERIAL}" >> "${FAKE_ADB_SCENARIO}/devices.txt"
fi

# Record the signal, then stay alive. The suite asserts on the absence of these lines, so
# a regression that lets the terminal signal through cannot pass silently.
#
# The TERM case is also written to the shared call log, next to the bridge daemon's own
# teardown lines. Two events in one ordered log are what make "the emulator was signalled
# before the bridge was closed" a checkable fact rather than an inference from two files
# written at different moments.
trap 'printf "TERM\n" >> "${STATE}/signals"; log_call "emulator: signal TERM"' TERM
trap 'printf "INT\n" >> "${STATE}/signals"' INT
trap 'printf "HUP\n" >> "${STATE}/signals"' HUP

while true; do
    sleep 0.2
done
"""

# Stand-in for the Remote ADB Bridge daemon.
#
# `emulator.sh` identity-checks its bridge with `ps -p <pid> -o args=` and requires
# `remote_adb_bridge` in the command line, so the marker below is load-bearing: it is what
# makes the spawned process identifiable to the script under test. It binds loopback only —
# a test has no business opening a real listener on every interface of the developer's
# machine, and nothing in these scenarios needs one.
_BRIDGE_DAEMON_CODE = r"""
import os, signal, socket, sys

MARKER = "remote_adb_bridge"  # identity marker read by `ps -o args=` in emulator.sh
SCENARIO = os.environ["FAKE_ADB_SCENARIO"]
LOG = os.path.join(SCENARIO, "calls.log")

opts = {}
argv = sys.argv[1:]
for index, arg in enumerate(argv):
    if arg.startswith("--") and index + 1 < len(argv) and not argv[index + 1].startswith("--"):
        opts[arg[2:]] = argv[index + 1]


def log(message):
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(message + "\n")


def shutdown(signum, frame):
    log("bridge: signal %d" % signum)
    raise SystemExit(0)


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)

listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(("127.0.0.1", int(opts.get("port", "0"))))
listener.listen(16)
bound_port = listener.getsockname()[1]

pid_file = opts.get("pid-file")
ready_file = opts.get("ready-file")
if pid_file:
    with open(pid_file, "w", encoding="utf-8") as handle:
        handle.write("%d\n" % os.getpid())
if ready_file:
    with open(ready_file, "w", encoding="utf-8") as handle:
        handle.write("%d\n" % bound_port)

log("bridge: listening on %d" % bound_port)
try:
    while True:
        try:
            conn, _ = listener.accept()
        except OSError:
            break
        conn.close()
finally:
    listener.close()
    for path in (pid_file, ready_file):
        if path and os.path.exists(path):
            os.remove(path)
    log("bridge: exited")
"""

_FAKE_PYTHON = r"""#!/usr/bin/env bash
# Fake `python3` for the runner harness. It only intercepts the bridge module: every other
# invocation (the config resolver, and anything else a runner might shell out to) is handed
# to the real interpreter, so the runners under test keep working.
set -u

if [[ "${1:-}" == "-m" && "${2:-}" == "boss_agent.services.remote_adb_bridge" ]]; then
    shift 2
    if [[ "${1:-}" == "--print-lan-ip" ]]; then
        printf '%s\n' "${FAKE_LAN_IP}"
        exit 0
    fi
    # The harness token rides into the daemon's argv so the harness can find and reap it
    # later. `exec` keeps the pid, so the pid the script records owns the port.
    exec "${FAKE_ADB_PYTHON}" -c "${FAKE_BRIDGE_DAEMON_CODE}" -- \
        --harness-token "${FAKE_BRIDGE_TOKEN}" "$@"
fi

exec "${FAKE_ADB_PYTHON}" "$@"
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
        for name, body in (("adb", _FAKE_ADB), ("python3", _FAKE_PYTHON)):
            fake = self.bin_dir / name
            fake.write_text(body.replace("__HANG_SECONDS__", str(HANG_SECONDS)), encoding="utf-8")
            fake.chmod(0o755)

        # A tag carried in the argv of every bridge this harness starts. The bridge is spawned
        # by the script under test, so the harness holds no handle on it: the tag is the only
        # way to find and reap it again.
        self.token = f"harness-{os.getpid()}-{id(self):x}"
        # A port of this harness's own. The scripts' bridge helpers probe and reclaim
        # `REMOTE_ADB_PORT`, so inheriting the real 6555 would let a test signal the
        # developer's actual bridge daemon.
        self.bridge_port = free_port()
        self._spawned: list[subprocess.Popen[bytes]] = []

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

    def emulator_launch_log(self, state_dir: Path) -> str:
        """Every launch the fake emulator recorded, one per line."""
        launch_log = state_dir / "log"
        return launch_log.read_text(encoding="utf-8") if launch_log.exists() else ""

    # --- bridge ---------------------------------------------------------------
    def bridge_files(self) -> tuple[Path, Path]:
        dot_boss = self.runtime_root / ".boss_agent"
        return dot_boss / "remote_bridge.pid", dot_boss / "remote_bridge.ready"

    def start_bridge(self, timeout: float = 5.0) -> subprocess.Popen[bytes]:
        """Start the fake bridge daemon directly, standing in for one already running.

        A test that needs a *running* bridge must not obtain it through the script under test:
        `start` would also launch an emulator. The spawned process owns a real port and a
        real pidfile, so every later observation (`status`, `stop`) reads real state.
        """
        pid_file, ready_file = self.bridge_files()
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _BRIDGE_DAEMON_CODE,
                "--",
                "--harness-token",
                self.token,
                "--port",
                str(self.bridge_port),
                "--pid-file",
                str(pid_file),
                "--ready-file",
                str(ready_file),
            ],
            env={**os.environ, "FAKE_ADB_SCENARIO": str(self.scenario)},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._spawned.append(process)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if ready_file.exists() and process.poll() is None:
                return process
            time.sleep(0.02)
        raise AssertionError(f"the fake bridge never became ready within {timeout}s")

    def bridge_pid(self) -> int | None:
        pid_file, _ = self.bridge_files()
        if not pid_file.exists():
            return None
        return int(pid_file.read_text(encoding="utf-8").strip())

    def port_is_bound(self) -> bool:
        with socket.socket() as probe:
            probe.settimeout(0.5)
            return probe.connect_ex(("127.0.0.1", self.bridge_port)) == 0

    # --- scenario scripting -------------------------------------------------
    def script(
        self,
        devices: Sequence[tuple[str, str]] = (),
        *,
        avd_name: str | None = None,
        boot_completed: str | None = None,
        serial_avd_names: Mapping[str, str] | None = None,
        serial_boot_completed: Mapping[str, str] | None = None,
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
        for serial, name in (serial_avd_names or {}).items():
            (self.scenario / f"avd_name.{serial}").write_text(f"{name}\n", encoding="utf-8")
        for serial, state in (serial_boot_completed or {}).items():
            (self.scenario / f"boot_completed.{serial}").write_text(f"{state}\n", encoding="utf-8")
        for hang in hangs:
            (self.scenario / f"hang.{hang}").write_text("", encoding="utf-8")
        for stubborn_call in stubborn:
            (self.scenario / f"stubborn.{stubborn_call}").write_text("", encoding="utf-8")

    def calls(self) -> str:
        """Every recorded call, in order.

        The fake bridge daemon logs into this same file, so its start/teardown lines
        interleave with the `adb` calls that have to bracket them — which is what makes
        "this happened before that" a checkable ordering rather than a claim.
        """
        calls_log = self.scenario / "calls.log"
        return calls_log.read_text(encoding="utf-8") if calls_log.exists() else ""

    def call_index(self, needle: str) -> int:
        """Position of the first recorded call containing `needle`, or -1 when absent."""
        for index, line in enumerate(self.calls().splitlines()):
            if needle in line:
                return index
        return -1

    def hang_pid(self, key: str) -> int:
        """PID of the still-running fake adb call recorded under `key`."""
        return int((self.scenario / f"{key}.hangpid").read_text(encoding="utf-8").strip())

    # --- ad-hoc processes -----------------------------------------------------
    def spawn_process(self, argv: Sequence[str]) -> subprocess.Popen[bytes]:
        """Spawn a live process whose command line is exactly `argv`.

        `emulator.sh` identifies emulator processes by pattern-matching `ps` output, so a
        test has to be able to hand the script a real process wearing a real command line.
        The harness environment is passed through, because a fake binary launched this way is
        a shell script with `set -u` and dies instantly on an unset `FAKE_ADB_PYTHON`.
        """
        process = subprocess.Popen(
            list(argv),
            env=self.command_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._spawned.append(process)
        return process

    def _tagged_pids(self) -> list[int]:
        """PIDs of live processes this harness put on the machine.

        Two tags, because the two kinds of process identify themselves differently: a bridge
        daemon is `exec`ed into a real interpreter and keeps only the argv the harness gave
        it, while a fake `adb` or `emulator` keeps the fake bin directory in its argv.
        """
        listing = subprocess.run(
            ["ps", "-eo", "pid=,args="], capture_output=True, text=True, check=False
        ).stdout
        pids: list[int] = []
        for line in listing.splitlines():
            pid_text, _, args = line.strip().partition(" ")
            if pid_text.isdigit() and (self.token in args or str(self.bin_dir) in args):
                pids.append(int(pid_text))
        return pids

    def shutdown(self) -> None:
        """Reap every process this harness started; never leak one into the suite."""
        for process in self._spawned:
            if process.poll() is None:
                with contextlib.suppress(OSError):
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        self._spawned.clear()

        # Anything the script under test launched itself is absent from `_spawned`; sweep for
        # it, or a bridge daemon keeps owning its port into the next test.
        for pid in self._tagged_pids():
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.kill(pid, sig)
                except (ProcessLookupError, PermissionError):
                    break
                if sig is signal.SIGTERM:
                    time.sleep(0.1)

    # --- execution ----------------------------------------------------------
    def command_env(self, env: dict[str, str] | None = None) -> dict[str, str]:
        command_env = dict(os.environ)
        command_env["PATH"] = f"{self.bin_dir}{os.pathsep}{command_env.get('PATH', '')}"
        command_env["FAKE_ADB_SCENARIO"] = str(self.scenario)
        command_env["FAKE_ADB_PYTHON"] = sys.executable
        command_env["FAKE_BRIDGE_DAEMON_CODE"] = _BRIDGE_DAEMON_CODE
        command_env["FAKE_BRIDGE_TOKEN"] = self.token
        command_env["FAKE_LAN_IP"] = FAKE_LAN_IP
        command_env["FAKE_AVD_NAME"] = TARGET_AVD
        command_env["FAKE_EMULATOR_SERIAL"] = FAKE_EMULATOR_SERIAL
        command_env["ANDROID_AVD"] = TARGET_AVD
        # Keep every knob that would otherwise make the scenario depend on this machine: the
        # bridge port (never the shared 6555) and the LAN address the scripts advertise.
        command_env["REMOTE_ADB_PORT"] = str(self.bridge_port)
        command_env["TARGET_ADB_PORT"] = "5555"
        command_env["HOST_LAN_IP"] = FAKE_LAN_IP
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
