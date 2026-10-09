#!/usr/bin/env bash
# ==============================================================================
# Boss Agent Mobile - Dedicated Android Virtual Device (AVD) Runner
# ==============================================================================
# Manages the dedicated Android Virtual Device with boot synchronization,
# persistent logging, and live log auto-attach.
#
# Usage:
#   ./emulator.sh                     # Start dedicated AVD in background and attach to logs
#   ./emulator.sh start               # Start dedicated AVD in background and attach to logs
#   ./emulator.sh start --daemon      # Start dedicated AVD in background (do not attach)
#   ./emulator.sh start --foreground  # Start dedicated AVD in foreground
#   ./emulator.sh status              # Check if dedicated AVD is online and booted
#   ./emulator.sh status --fix        # Same check, but repair the ADB bridge if it is down
#   ./emulator.sh list                # List all installed local AVDs
#   ./emulator.sh logs                # Attach to live log stream of running AVD
#   ./emulator.sh stop                # Stop the AVD's services (bridge); keep the AVD process
#   ./emulator.sh restart             # Reuse online AVD (restarting its ADB bridge), else restart and attach
#   ./emulator.sh restart --daemon    # Same decision, in background
#   ./emulator.sh reconnect           # Restore LAN ADB access without restarting the AVD
#
# Lifecycle:
#   The AVD is machine-wide infrastructure, not a child of this script. It is started in a
#   session and process group of its own, so it keeps running after this script exits, after
#   Ctrl+C detaches the log stream, and after a group- or session-wide cleanup.
#
#   No command here stops the AVD process. `stop` and `restart` release the services this
#   runner owns — the Remote ADB Bridge and its LAN transport — and nothing else, so
#   stopping one runner never costs the next one a 30-60s cold boot. A shutdown signal
#   aimed at a Virtual Device Session is the user's to give, by hand.
#
#   Repair, not restart: a dead Remote ADB Bridge is the common failure that *looks* healthy
#   from the console while every remote client is locked out. `reconnect` fixes that without
#   disturbing a running AVD, so it is the answer whenever the AVD itself is fine and only
#   reachability is not.
#
# Environment:
#   ADB_QUERY_TIMEOUT_SEC      Wall-clock bound for a single adb query (default 2).
#                              The knob only tightens it: clamped to 1-2 seconds, and
#                              with the SIGKILL escalation a query never exceeds 2.5s,
#                              so an offline or unresponsive device cannot stall the runner.
# ==============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

# Shared process-lifecycle primitives and the one config read.
# shellcheck source=runner_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/runner_lib.sh"

mkdir -p ".boss_agent"
RUNNER_TMP_DIR="${ROOT_DIR}/.boss_agent/run"
mkdir -p "${RUNNER_TMP_DIR}" 2>/dev/null || RUNNER_TMP_DIR="${TMPDIR:-/tmp}"
# Opportunistically purge stale bounded query temp files older than 5 minutes.
find "${RUNNER_TMP_DIR}" -maxdepth 1 -name "boss_agent_bounded.*" -mmin +5 -delete 2>/dev/null || true

PID_FILE=".boss_agent/emulator.pid"
LOG_FILE=".boss_agent/emulator.log"
BRIDGE_PID_FILE=".boss_agent/remote_bridge.pid"
BRIDGE_READY_FILE=".boss_agent/remote_bridge.ready"
BRIDGE_LOG_FILE=".boss_agent/remote_bridge.log"
REMOTE_ADB_PORT="${REMOTE_ADB_PORT:-6555}"
TARGET_ADB_PORT="${TARGET_ADB_PORT:-5555}"

EMULATOR_BIN="$(runner_find_binary emulator "${ANDROID_HOME:-}/emulator/emulator" "${ANDROID_SDK_ROOT:-}/emulator/emulator" "$HOME/Library/Android/sdk/emulator/emulator")"
ADB_BIN="$(runner_find_binary adb "${ANDROID_HOME:-}/platform-tools/adb" "${ANDROID_SDK_ROOT:-}/platform-tools/adb" "$HOME/Library/Android/sdk/platform-tools/adb")"
PYTHON_BIN="$(runner_find_binary "${PYTHON_BIN:-}" "${ROOT_DIR}/.venv/bin/python3" python3 python)"

# --- Terminal signal isolation -------------------------------------------------
# The AVD is machine-wide infrastructure that must outlive the script that started it, but
# a background job started with plain `nohup ... &` stays in the *runner's* process group
# and session, and keeps the terminal as its controlling terminal.
#
# Note that `nohup` alone is not the gap it looks like, and the distinction matters when
# reasoning about what this protects. POSIX already makes a background job started from a
# non-interactive shell ignore SIGINT and SIGQUIT, so Ctrl+C was never the hazard here. The
# real exposure is everything that acts on the *group* or the *session*: supervisor and CI
# teardown that sweeps a process group, a harness that reaps what it spawned, and terminal
# hangup delivered to the session. A process group of its own is out of reach of all of them.

# Start "$2" (with "$@"[2:]) in a brand-new session, detached from this process's terminal
# group, logging to the file given as "$1".
#
# The new session is made with the setsid(2) syscall rather than a `setsid` binary, because
# macOS ships no setsid(1) and this script has to behave identically on every developer's
# machine. `nohup` still wraps the call so the child additionally ignores SIGHUP.
#
# The child is launched with its own PID and then `exec`s the target, so the PID printed
# here is the PID of the emulator (or bridge) itself — not a wrapper that could die first
# and leave a stale pidfile pointing at a recycled PID.
detached_spawn() {
    local SPAWN_LOG="$1"
    shift
    if [[ -z "${PYTHON_BIN}" ]]; then
        # No Python available: fall back to the old behaviour rather than refusing to
        # start. `nohup` still covers the SIGHUP case. This does *not* restore session
        # isolation, and a call site that needs Python anyway (the remote ADB bridge) will
        # simply fail to exec, which it already degraded around before this helper existed.
        nohup "$@" </dev/null >> "${SPAWN_LOG}" 2>&1 &
        disown $! 2>/dev/null || true
        echo "$!"
        return 0
    fi

    nohup "${PYTHON_BIN}" -c \
        'import os, sys
try:
    os.setsid()
except OSError:
    # setsid() only fails when this child is already a process-group leader, which takes
    # job control to be on — not the case for a background job in this non-interactive
    # script. Even if it were reached, only the process group would be new: the session and
    # the controlling terminal are still shared with the runner, so this is degraded
    # isolation rather than none at all.
    pass
os.execvp(sys.argv[1], sys.argv[1:])' \
        "$@" </dev/null >> "${SPAWN_LOG}" 2>&1 &
    local SPAWNED_PID=$!
    disown "${SPAWNED_PID}" 2>/dev/null || true
    echo "${SPAWNED_PID}"
}

# --- Bounded ADB inspection ---------------------------------------------------
# A wedged adb server, a device stuck in `offline`, or an unresponsive adbd must never
# freeze emulator.sh: every adb query below goes through `bounded_run` with a hard
# wall-clock bound, so serial resolution, `status`, and `start` always return.

# Echo an integer clamped into [MIN, MAX]; a non-numeric value falls back to DEFAULT.
clamp_int() {
    local VALUE="$1"
    local DEFAULT="$2"
    local MIN="$3"
    local MAX="$4"
    [[ "${VALUE}" =~ ^[0-9]+$ ]] || VALUE="${DEFAULT}"
    if (( VALUE < MIN )); then
        VALUE="${MIN}"
    elif (( VALUE > MAX )); then
        VALUE="${MAX}"
    fi
    echo "${VALUE}"
}

# Patience for a single adb query. The environment knob only ever tightens it: SIGTERM fires
# here and the SIGKILL escalation follows 0.5s later, so one query can hold the script for
# at most 2.5s - inside the agreed 3-second contract.
ADB_QUERY_TIMEOUT_SEC="$(clamp_int "${ADB_QUERY_TIMEOUT_SEC:-2}" 2 1 2)"

# Run one external command under a hard wall-clock limit, printing its stdout (empty when
# the command had to be killed).
#
# When Python is available, the command is executed directly in memory without writing any
# temporary files to disk. This completely prevents temporary file leaks, avoids macOS
# privileged desktop-services trash prompts in system temp directories, and cleanly escalates
# from SIGTERM to SIGKILL on stubborn processes.
# A pure-shell fallback is retained for environments without Python.
bounded_run() {
    local TIMEOUT_SEC="$1"
    shift
    if [[ -n "${PYTHON_BIN}" ]]; then
        "${PYTHON_BIN}" -c '
import contextlib, os, signal, subprocess, sys

timeout = float(sys.argv[1])
cmd = sys.argv[2:]
proc = subprocess.Popen(
    cmd,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
)
try:
    stdout, _ = proc.communicate(timeout=timeout)
    sys.stdout.buffer.write(stdout)
    sys.exit(proc.returncode)
except subprocess.TimeoutExpired:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    sys.exit(124)
' "${TIMEOUT_SEC}" "$@"
        return $?
    fi

    local OUT_FILE
    OUT_FILE="$(mktemp "${RUNNER_TMP_DIR}/boss_agent_bounded.XXXXXX")"
    trap '/bin/rm -f "${OUT_FILE}" 2>/dev/null || true; exit 143' INT TERM
    trap '/bin/rm -f "${OUT_FILE}" 2>/dev/null || true' EXIT

    "$@" >"${OUT_FILE}" 2>/dev/null &
    local CMD_PID=$!

    # The watchdog must not inherit this process's stdout: when `bounded_run` is used in a
    # command substitution, a surviving sleeper holding the pipe would keep the caller
    # waiting until it wakes up.
    (
        sleep "${TIMEOUT_SEC}"
        if runner_process_alive "${CMD_PID}"; then
            kill -TERM "${CMD_PID}" 2>/dev/null || true
            sleep 0.5
            kill -KILL "${CMD_PID}" 2>/dev/null || true
        fi
    ) >/dev/null 2>&1 &
    local WATCHDOG_PID=$!

    local EXIT_CODE=0
    wait "${CMD_PID}" 2>/dev/null || EXIT_CODE=$?
    # Reap the watchdog before it can fire at a recycled PID.
    kill -TERM "${WATCHDOG_PID}" 2>/dev/null || true
    wait "${WATCHDOG_PID}" 2>/dev/null || true

    # Report a query we had to kill as 124, the `timeout(1)` convention for "timed out", so a
    # caller can tell "the bound was hit" apart from "adb itself failed" (adb's own status).
    # Both signals land here: `timeout` distinguishes TERM (124) from KILL (137), which is
    # noise for a caller whose only question is whether adb answered.
    if (( EXIT_CODE == 143 || EXIT_CODE == 137 )); then
        EXIT_CODE=124
    fi

    cat "${OUT_FILE}" 2>/dev/null || true
    /bin/rm -f "${OUT_FILE}" 2>/dev/null || true
    trap - EXIT INT TERM
    return "${EXIT_CODE}"
}

# Bounded adb query: prints captured stdout and exits 0 when adb answered, 124 when the bound
# was hit, or adb's own status when it failed. Callers that only want the answer append
# `|| true` and read the empty output as "did not answer".
adb_query() {
    if [[ -z "${ADB_BIN}" ]]; then
        return 0
    fi
    bounded_run "${ADB_QUERY_TIMEOUT_SEC}" "${ADB_BIN}" "$@"
}

# Bounded `adb shell getprop <key>`: empty when the device does not answer in time.
adb_getprop() {
    local SERIAL="$1"
    local KEY="$2"
    local RAW
    RAW="$(adb_query -s "${SERIAL}" shell getprop "${KEY}" || true)"
    printf '%s\n' "${RAW}" | tr -d '\r\n'
}

# Bounded `adb -s <serial> emu avd name`: the AVD name, or empty when the device does not
# answer in time.
adb_avd_name() {
    local SERIAL="$1"
    local RAW=""
    if [[ "${SERIAL}" =~ ^emulator-[0-9]+$ ]]; then
        RAW="$(adb_query -s "${SERIAL}" emu avd name || true)"
    else
        RAW="$(adb_query -s "${SERIAL}" shell getprop ro.boot.qemu.avd_name || true)"
    fi
    printf '%s\n' "${RAW}" | head -n 1 | tr -d '\r\n'
}

resolve_target_avd() {
    if [[ -n "${TARGET_AVD_OVERRIDE:-}" ]]; then
        echo "${TARGET_AVD_OVERRIDE}"
        return 0
    fi

    if [[ -n "${ANDROID_AVD:-}" ]]; then
        echo "${ANDROID_AVD}"
        return 0
    fi

    if [[ -n "${AVD_NAME:-}" ]]; then
        echo "${AVD_NAME}"
        return 0
    fi

    # One config read, through the library.
    local CONF_AVD
    CONF_AVD="$(runner_config_value avd_name "")"
    if [[ -n "${CONF_AVD}" ]]; then
        echo "${CONF_AVD}"
        return 0
    fi

    if [[ -n "${EMULATOR_BIN}" ]]; then
        local LIST_AVDS
        LIST_AVDS="$("${EMULATOR_BIN}" -list-avds 2>/dev/null || true)"
        if echo "${LIST_AVDS}" | grep -q "^boss_avd_arm64$"; then
            echo "boss_avd_arm64"
            return 0
        fi
        local FIRST_AVD
        FIRST_AVD="$(echo "${LIST_AVDS}" | head -n 1)"
        if [[ -n "${FIRST_AVD}" ]]; then
            echo "${FIRST_AVD}"
            return 0
        fi
    fi

    echo "boss_avd_arm64"
}

TARGET_AVD_OVERRIDE=""

ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --avd)
            TARGET_AVD_OVERRIDE="$2"
            shift 2
            ;;
        *)
            ARGS+=("$1")
            shift
            ;;
    esac
done
set -- "${ARGS[@]:-start}"

TARGET_AVD="$(resolve_target_avd)"

get_running_device_serial() {
    if [[ -z "${ADB_BIN}" ]]; then
        echo ""
        return 0
    fi

    local RAW_DEVICES DEV_LIST
    RAW_DEVICES="$(adb_query devices || true)"
    # Only devices in the `device` state are queried: an `offline` (or otherwise broken)
    # transport cannot report its AVD name and blocks the adb client until it times out.
    #
    # Candidates are ordered, not merely filtered: the Remote ADB Bridge forwards to the same
    # adbd, so its `<lan-ip>:<port>` transport reports the same AVD name as the local emulator
    # and both match. The native `emulator-<port>` transport is probed first because it exists
    # on its own — the LAN one only appears while the bridge is up, so preferring it would let
    # a bridge outage make a perfectly healthy AVD look absent, and `stop` would then report
    # nothing running over a device that is still there. `adb devices` order is not a
    # contract, so the priority is made explicit here rather than left to whichever transport
    # the server happened to list first.
    DEV_LIST="$(printf '%s\n' "${RAW_DEVICES}" \
        | awk '($1 ~ /^emulator-[0-9]+$/ || $1 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+:[0-9]+$/) && $2 == "device" {
            print ($1 ~ /^emulator-[0-9]+$/ ? 0 : 1) "\t" $1 }' \
        | LC_ALL=C sort -k1,1n -k2,2 \
        | cut -f2-)"

    # Every candidate is probed, however slow: giving up on the scan early could miss the
    # dedicated AVD sitting behind unresponsive siblings. The per-query bound, not a global
    # budget, is what keeps this responsive.
    local dev
    for dev in ${DEV_LIST}; do
        local AVD_NAME_FOUND
        AVD_NAME_FOUND="$(adb_avd_name "${dev}")"
        if [[ "${AVD_NAME_FOUND}" == "${TARGET_AVD}" ]]; then
            echo "${dev}"
            return 0
        fi
    done
    echo ""
}

# The PID an attached log stream should watch, or the literal `active` when there is
# nothing it can honestly watch.
#
# `emulator.pid` is written *only* by the launch path, so an AVD that was started earlier —
# or reused by a later `start`, which never reaches that path — arrives here holding no
# handle at all. `active` is not a PID, and the watcher's contract for an unwatchable handle
# is to degrade to a bare `tail -f`, which silently disables the very stop notice the
# operator attached the stream to see. The AVD's own live process is a handle that does
# exist, so it is adopted here.
#
# Only an *unambiguous* one is. `emulator_process_pids` matches both the `emulator` front-end
# and the QEMU backend, and the two need not exit together: watching a PID that outlives the
# AVD would announce a stop that had not happened, and watching one that leaves first would
# announce it while the device is still up. Two live matches are therefore no information,
# and the caller keeps the old `active` degradation rather than guess.
watched_avd_pid() {
    local PID
    PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
    if [[ "${PID}" =~ ^[0-9]+$ ]] && runner_process_alive "${PID}"; then
        echo "${PID}"
        return 0
    fi

    local CANDIDATE LIVE=0 SOLE=""
    for CANDIDATE in $(emulator_process_pids); do
        [[ "${CANDIDATE}" =~ ^[0-9]+$ ]] || continue
        LIVE=$((LIVE + 1))
        SOLE="${CANDIDATE}"
    done
    if [[ ${LIVE} -eq 1 ]]; then
        # Repair the handle while it is in hand. This file is what `status` and every later
        # attach read, and leaving it missing is how the same silence comes back.
        echo "${SOLE}" > "${PID_FILE}"
        echo "${SOLE}"
        return 0
    fi

    echo "active"
}

attach_logs() {
    local SERIAL="$1"
    local ALREADY_RUNNING="${2:-0}"
    local AVD_PID
    AVD_PID="$(watched_avd_pid)"
    local BRIDGE_PID
    BRIDGE_PID="$(get_running_bridge_pid)"

    if [[ "${ALREADY_RUNNING}" == "1" ]]; then
        echo "ℹ️ Dedicated AVD '${TARGET_AVD}' is already running (${SERIAL}, PID: ${AVD_PID})"
    fi
    echo "👀 Attaching to live log stream (${LOG_FILE})... (Press Ctrl+C to detach)"
    echo "----------------------------------------------------------------------"

    trap 'runner_watch_detach; echo -e "\n👋 Detached from emulator logs (AVD is still running in background)."; exit 0' INT TERM

    # The same liveness watch the four process-backed runners use (ticket #425), reached
    # with a handle rather than a runner-owned foreground child.
    #
    # Two handles, and which one is the *service* is the whole point. A shutdown signal from
    # another terminal goes to the Remote ADB Bridge — no command here stops the AVD — so a
    # stream watching only the AVD is watching the one process that never goes away, and
    # the operator's terminal can never unblock from a `stop` it was attached to see. The
    # bridge is therefore the primary handle, with its own pidfile so a `restart` from
    # another terminal is announced as the stop-and-restart it is.
    #
    # The AVD is kept as the second handle, and for a reason that outlives this script
    # knowing about stop commands: the AVD is the writer of the log being displayed. If it
    # dies on its own — a crash, or a user closing it by hand, which is the only way it can
    # be closed now — the stream is following a file nobody is writing, and ending is the
    # honest thing to do. It is a plain stop rather than a restart: the bridge's pidfile
    # says nothing about whether the AVD came back, so claiming a restart from it would be
    # the same invented evidence the primary-handle check refuses to invent.
    #
    # An unwatchable handle is no information at all, never a death (see
    # `runner_pid_watchable`), so a stream with no bridge to watch degrades to still
    # following the AVD and one with no AVD to watch still follows the bridge. Neither
    # degrades to a bare `tail -f` unless there is genuinely nothing to watch.
    runner_watch_log_stream \
        "${BRIDGE_PID:-active}" \
        "${LOG_FILE}" \
        "Remote ADB Bridge" 30 "${BRIDGE_PID_FILE}" \
        "${AVD_PID}" "Dedicated AVD ${TARGET_AVD}"
}

get_primary_lan_ip() {
    local LAN_IP=""
    if [[ -n "${HOST_LAN_IP:-}" ]]; then
        echo "${HOST_LAN_IP}"
        return 0
    fi
    local PY_BIN="${PYTHON_BIN:-$(runner_find_binary "${ROOT_DIR}/.venv/bin/python3" python3 python)}"
    if [[ -n "${PY_BIN}" ]]; then
        LAN_IP="$(PYTHONPATH="${ROOT_DIR}/src:${PYTHONPATH:-}" "${PY_BIN}" -m boss_agent.services.remote_adb_bridge --print-lan-ip 2>/dev/null || true)"
    fi
    if [[ -z "${LAN_IP}" || "${LAN_IP}" =~ ^127\. ]]; then
        LAN_IP="$(ifconfig 2>/dev/null | grep -E 'inet[[:space:]]+(192\.168|10\.|172\.(1[6-9]|2[0-9]|3[01]))\.' | awk '{print $2}' | head -n 1 || true)"
    fi
    if [[ -z "${LAN_IP}" ]]; then
        LAN_IP="127.0.0.1"
    fi
    echo "${LAN_IP}"
}

# The `<lan-ip>:<port>` transport the Remote ADB Bridge publishes, or empty when this host has
# no non-loopback address to publish it on. One definition, so every caller agrees on which
# transport "the LAN connection" means.
lan_serial() {
    local LAN_IP
    LAN_IP="$(get_primary_lan_ip)"
    if [[ -z "${LAN_IP}" || "${LAN_IP}" == "127.0.0.1" ]]; then
        return 0
    fi
    printf '%s:%s\n' "${LAN_IP}" "${REMOTE_ADB_PORT:-6555}"
}

# `adb connect` the transport, then wait for it to answer. Idempotent: connecting a
# transport that is already up is a no-op on a real adb server, and the readiness wait is
# what actually decides whether it worked.
connect_lan_adb() {
    local SERIAL="$1"
    adb_query connect "${SERIAL}" >/dev/null 2>&1 || true
    for _ in {1..20}; do
        if [[ "$(adb_getprop "${SERIAL}" sys.boot_completed)" == "1" ]]; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

# Whether the transport is currently listed by adb as usable. A bridge that is listening but
# not connected leaves remote management silently broken, so presence in the device list is
# the question -- not whether the port is open.
lan_adb_connected() {
    local SERIAL="$1"
    adb_query devices 2>/dev/null \
        | awk -v s="${SERIAL}" '$1 == s && $2 == "device" { found = 1 } END { exit !found }'
}

# A one-word verdict on the LAN path from facts the caller already holds: the bridge PID and
# whether the transport is listed. Kept pure, and shared, so `status` and `reconnect` cannot
# drift into disagreeing about what "healthy" means -- a diagnostic that reports a different
# verdict from the repair it recommends is worse than no diagnostic.
#
# Both halves matter because either one alone hides a real outage: a bridge that is down
# cannot carry a connection, and a bridge that is up but unconnected looks perfectly healthy
# from the console while every remote client is locked out.
lan_verdict() {
    local BRIDGE_PID="${1:-}"
    local CONNECTED="${2:-0}"
    if [[ -z "${BRIDGE_PID}" ]]; then
        echo "bridge-down"
    elif [[ "${CONNECTED}" != "1" ]]; then
        echo "lan-disconnected"
    else
        echo "ok"
    fi
}

# `lan_verdict` for callers that have to gather the facts themselves.
lan_health() {
    local SERIAL
    SERIAL="$(lan_serial)"
    if [[ -z "${SERIAL}" ]]; then
        echo "no-lan-address"
        return 0
    fi
    local BRIDGE_PID CONNECTED=0
    BRIDGE_PID="$(get_running_bridge_pid)"
    if [[ -n "${BRIDGE_PID}" ]] && lan_adb_connected "${SERIAL}"; then
        CONNECTED=1
    fi
    lan_verdict "${BRIDGE_PID}" "${CONNECTED}"
}

# Bring the LAN path back without touching an AVD that is already healthy.
#
# This is the cheap repair, and it is deliberately the *only* one: restarting the AVD would
# also restore connectivity, at the cost of the run the operator was trying to protect. So
# when there is no AVD to serve, it refuses rather than starting a bridge that cannot work.
cmd_reconnect() {
    echo "🔌 Checking the LAN path to dedicated AVD '${TARGET_AVD}'..."

    local SERIAL
    SERIAL="$(get_running_device_serial)"
    if [[ -z "${SERIAL}" ]]; then
        echo "🔴 Dedicated AVD '${TARGET_AVD}' is NOT RUNNING; there is nothing to reconnect to."
        echo "💡 Start it first: ./emulator.sh start --daemon"
        return 1
    fi

    local BOOT
    BOOT="$(adb_getprop "${SERIAL}" sys.boot_completed)"
    if [[ "${BOOT}" != "1" ]]; then
        echo "🟡 Dedicated AVD '${TARGET_AVD}' is still BOOTING (${SERIAL}, sys.boot_completed='${BOOT}')."
        echo "💡 Wait for the boot to finish, then retry: ./emulator.sh status"
        return 1
    fi

    local LAN_SERIAL
    LAN_SERIAL="$(lan_serial)"
    if [[ -z "${LAN_SERIAL}" ]]; then
        echo "⚠️ No non-loopback LAN IP detected; the AVD cannot be reached over the network."
        return 1
    fi

    local HEALTH
    HEALTH="$(lan_health)"
    if [[ "${HEALTH}" == "ok" ]]; then
        echo "🟢 LAN ADB connection is already healthy (${LAN_SERIAL}); nothing to repair."
        return 0
    fi

    echo "⚠️ LAN path is unhealthy (${HEALTH}); repairing it without restarting the AVD..."
    # Idempotent by construction: a bridge that is already listening is left alone.
    start_remote_bridge
    if connect_lan_adb "${LAN_SERIAL}"; then
        echo "🟢 LAN ADB connection restored: ${LAN_SERIAL} (ONLINE and READY)!"
        return 0
    fi
    echo "⚠️ Could not verify ${LAN_SERIAL} after repair. Check ${BRIDGE_LOG_FILE}."
    return 1
}

get_running_bridge_pid() {
    local PORT="${REMOTE_ADB_PORT:-6555}"
    if [[ -f "${BRIDGE_PID_FILE}" ]]; then
        local PID
        PID="$(cat "${BRIDGE_PID_FILE}" 2>/dev/null || true)"
        if [[ -n "${PID}" ]] && runner_process_alive "${PID}"; then
            local CMD
            CMD="$(ps -p "${PID}" -o args= 2>/dev/null || true)"
            if [[ "${CMD}" == *"remote_adb_bridge"* ]]; then
                echo "${PID}"
                return 0
            fi
        fi
        # PID file was stale or belongs to a recycled/unrelated process
        rm -f "${BRIDGE_PID_FILE}" "${BRIDGE_READY_FILE}"
    fi

    # Fallback to listening port: verify listener process actually matches bridge
    local PORT_PID
    PORT_PID="$(runner_port_listener_pid "${PORT}")"
    if [[ -n "${PORT_PID}" ]]; then
        local CMD
        CMD="$(ps -p "${PORT_PID}" -o args= 2>/dev/null || true)"
        if [[ "${CMD}" == *"remote_adb_bridge"* ]]; then
            echo "${PORT_PID}" > "${BRIDGE_PID_FILE}"
            echo "${PORT_PID}"
            return 0
        fi
    fi
    echo ""
}

start_remote_bridge() {
    local PORT="${REMOTE_ADB_PORT:-6555}"
    local TARGET_PORT="${TARGET_ADB_PORT:-5555}"

    local RUNNING_PID
    RUNNING_PID="$(get_running_bridge_pid)"
    if [[ -n "${RUNNING_PID}" ]]; then
        return 0
    fi

    # Reclaim the port from an *orphaned bridge of this runner* — and from nothing else.
    #
    # This used to match on `ps -o comm=` being `python` or `socat` and SIGTERM/SIGKILL
    # whatever held 6555. That is a machine-wide hazard on a developer box: the bridge's
    # `comm` is just `python3`, so "it is python" identifies nothing, and a plain
    # `./emulator.sh` — which is supposed to only *attach* — could kill an unrelated
    # process and take another runner's service down with it. It is the same mistake as
    # trusting a recycled PID, one level up: recognising a process by its interpreter
    # rather than by what it is for.
    #
    # So the only process this will reclaim is one whose full command line proves it is
    # this runner's own `remote_adb_bridge` — the genuine orphan this cleanup exists for.
    # Anything else on the port is reported and left running, because a port conflict is
    # something the user can resolve with information, and a killed stranger is not
    # something they can undo.
    if command -v lsof >/dev/null 2>&1; then
        local CONFLICT_PID
        CONFLICT_PID="$(lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN -t 2>/dev/null | head -n 1 || true)"
        if [[ -n "${CONFLICT_PID}" ]]; then
            local CONFLICT_ARGS
            CONFLICT_ARGS="$(ps -p "${CONFLICT_PID}" -o args= 2>/dev/null || true)"
            if [[ "${CONFLICT_ARGS}" == *"remote_adb_bridge"* ]]; then
                echo "⚠️ Port ${PORT} held by an orphaned Remote ADB Bridge (PID: ${CONFLICT_PID}). Reclaiming..."
                kill -TERM "${CONFLICT_PID}" 2>/dev/null || true
                sleep 0.5
                kill -KILL "${CONFLICT_PID}" 2>/dev/null || true
            else
                # Not ours, so not ours to kill. Naming it is the useful part: an operator
                # can act on "port 6555 is held by PID N" and not on a mysterious exit.
                echo "⚠️ Port ${PORT} is held by PID ${CONFLICT_PID}, which is not a Remote ADB Bridge."
                echo "   Leaving it alone — stop it yourself if it is stale, then re-run."
            fi
        fi
    fi

    rm -f "${BRIDGE_READY_FILE}"
    echo "🌉 Starting Remote ADB Bridge daemon (0.0.0.0:${PORT} -> 127.0.0.1:${TARGET_PORT})..."
    # Session-isolated for the same reason as the emulator: a bridge that dies with the
    # terminal leaves the AVD unreachable over LAN even though the device is still up.
    # `env` rather than a `PYTHONPATH=…` prefix, because a variable assignment applied to
    # the `detached_spawn` *function* call is how the override reaches the helper's own
    # commands, and keeping it on the exec chain instead makes the intent explicit and
    # independent of how a given bash version scopes assignments around function calls.
    # PYTHONPATH and the interpreter come from PYTHON_BIN, resolved once at the top of
    # this script through runner_find_binary — the same discovery every runner uses.
    local BRIDGE_PID
    BRIDGE_PID="$(detached_spawn \
        "${BRIDGE_LOG_FILE}" \
        env "PYTHONPATH=${ROOT_DIR}/src:${PYTHONPATH:-}" "${PYTHON_BIN}" \
        -m boss_agent.services.remote_adb_bridge \
        --host 0.0.0.0 \
        --port "${PORT}" \
        --target-host 127.0.0.1 \
        --target-port "${TARGET_PORT}" \
        --pid-file "${BRIDGE_PID_FILE}" \
        --ready-file "${BRIDGE_READY_FILE}")"

    local READY=0
    for _ in {1..30}; do
        if [[ -f "${BRIDGE_READY_FILE}" ]] && runner_process_alive "${BRIDGE_PID}"; then
            READY=1
            break
        fi
        sleep 0.1
    done
    if [[ ${READY} -eq 1 ]]; then
        echo "✅ Remote ADB Bridge daemon is active (PID: ${BRIDGE_PID}, Port: ${PORT})."
    else
        echo "⚠️ Remote ADB Bridge daemon started (PID: ${BRIDGE_PID}). Check ${BRIDGE_LOG_FILE}."
    fi
}

stop_remote_bridge() {
    local PORT="${REMOTE_ADB_PORT:-6555}"
    local LAN_IP
    LAN_IP="$(get_primary_lan_ip)"

    if [[ -n "${LAN_IP}" && "${LAN_IP}" != "127.0.0.1" ]]; then
        adb_query disconnect "${LAN_IP}:${PORT}" >/dev/null 2>&1 || true
    fi

    local PID
    PID="$(get_running_bridge_pid)"
    if [[ -n "${PID}" ]]; then
        kill -TERM "${PID}" 2>/dev/null || true
        sleep 0.3
        kill -KILL "${PID}" 2>/dev/null || true
        echo "ℹ️ Stopped Remote ADB Bridge daemon (PID: ${PID})."
    fi
    rm -f "${BRIDGE_PID_FILE}" "${BRIDGE_READY_FILE}"
}

ensure_lan_adb_connected() {
    start_remote_bridge
    local SERIAL
    SERIAL="$(lan_serial)"
    if [[ -z "${SERIAL}" ]]; then
        echo "⚠️ No non-loopback LAN IP detected; skipping auto-connect over LAN."
        return 0
    fi

    echo "🔌 Auto-connecting ADB to dedicated AVD via LAN (${SERIAL})..."
    if connect_lan_adb "${SERIAL}"; then
        echo "🟢 Dedicated AVD connected via LAN: ${SERIAL} (ONLINE and READY)!"
    else
        echo "⚠️ Unable to verify LAN connection to ${SERIAL}. Check ${BRIDGE_LOG_FILE}."
    fi
}

cmd_logs() {
    local SERIAL
    SERIAL="$(get_running_device_serial)"
    if [[ -z "${SERIAL}" ]]; then
        echo "🔴 Dedicated AVD '${TARGET_AVD}' is not running."
        if [[ -f "${LOG_FILE}" ]]; then
            echo "📄 Displaying recent logs from ${LOG_FILE}:"
            tail -n 30 "${LOG_FILE}"
        fi
        return 1
    fi
    attach_logs "${SERIAL}" 1
}

cmd_list() {
    if [[ -z "${EMULATOR_BIN}" ]]; then
        echo "❌ Error: 'emulator' binary not found in PATH or Android SDK." >&2
        exit 1
    fi
    echo "📱 Installed Android Virtual Devices (AVDs):"
    "${EMULATOR_BIN}" -list-avds | while read -r avd; do
        if [[ "${avd}" == "${TARGET_AVD}" ]]; then
            echo "  👉 ${avd} (Dedicated Target)"
        else
            echo "     ${avd}"
        fi
    done
}

cmd_status() {
    # `status` is polled by supervisors, so it stays read-only unless explicitly told to act.
    local FIX=0
    if [[ "${1:-}" == "--fix" ]]; then
        FIX=1
    fi

    echo "🔍 Checking Dedicated Android AVD ('${TARGET_AVD}') status..."

    if [[ -z "${ADB_BIN}" ]]; then
        echo "❌ Error: 'adb' binary not found in PATH or Android SDK." >&2
        return 1
    fi

    local SERIAL
    SERIAL="$(get_running_device_serial)"

    if [[ -z "${SERIAL}" ]]; then
        echo "🔴 Dedicated AVD '${TARGET_AVD}' is NOT RUNNING."
        return 1
    fi

    local BOOT_STATUS
    BOOT_STATUS="$(adb_getprop "${SERIAL}" sys.boot_completed)"

    if [[ "${BOOT_STATUS}" != "1" ]]; then
        echo "🟡 Dedicated AVD '${TARGET_AVD}' is BOOTING (${SERIAL}, sys.boot_completed='${BOOT_STATUS}')."
        return 1
    fi

    echo "🟢 Dedicated AVD '${TARGET_AVD}' is ONLINE and READY (${SERIAL})."
    echo "   Log file : ${LOG_FILE}"

    local PORT="${REMOTE_ADB_PORT:-6555}"
    local LAN_IP
    LAN_IP="$(get_primary_lan_ip)"
    local BRIDGE_PID
    BRIDGE_PID="$(get_running_bridge_pid)"

    if [[ -n "${BRIDGE_PID}" ]]; then
        echo "🟢 Remote ADB Bridge is LISTENING (PID: ${BRIDGE_PID}, Port: ${PORT}, LAN: ${LAN_IP}:${PORT})"
    else
        echo "⚪ Remote ADB Bridge is NOT RUNNING (Port: ${PORT})"
    fi

    local LAN_SERIAL CONNECTED=0 LAN_STATE
    LAN_SERIAL="$(lan_serial)"
    if [[ -z "${LAN_SERIAL}" ]]; then
        # There is no address to publish the bridge on, so the LAN path does not exist on this
        # host. Saying that plainly is the whole answer: recommending `reconnect` here would
        # be a diagnostic pointing at a repair that can only fail.
        LAN_STATE="no-lan-address"
    else
        if lan_adb_connected "${LAN_SERIAL}"; then
            echo "🟢 LAN ADB Connection is CONNECTED and READY (${LAN_SERIAL})"
            CONNECTED=1
        else
            echo "⚪ LAN ADB Connection is DISCONNECTED (${LAN_SERIAL})"
        fi
        LAN_STATE="$(lan_verdict "${BRIDGE_PID}" "${CONNECTED}")"
    fi

    case "${LAN_STATE}" in
        ok) ;;
        no-lan-address)
            echo "⚪ No non-loopback LAN IP detected; LAN ADB access is unavailable on this host."
            ;;
        *)
            # The AVD can be perfectly healthy while remote management is locked out, so a
            # broken LAN path gets a lever rather than just a report.
            if [[ ${FIX} -eq 1 ]]; then
                echo "🛠️ Self-heal requested (--fix): repairing the LAN path (${LAN_STATE})..."
                if cmd_reconnect; then
                    echo "🟢 LAN path repaired."
                else
                    echo "⚠️ Self-heal could not confirm the LAN path; see ${BRIDGE_LOG_FILE}."
                fi
            else
                echo "💡 Repair without restarting the AVD: ./emulator.sh reconnect  (or re-run with --fix)"
            fi
            ;;
    esac

    return 0
}

# The PIDs currently running the dedicated AVD, as a space-separated list.
#
# Two argv shapes have to be matched, because the Android emulator is only a front-end over a
# QEMU backend: `emulator ... @<avd>` is what this script launches, while the process that
# actually owns the AVD is `qemu-system-<arch>` carrying `-avd <name>` (or `avd_name=<name>`)
# in its argv. Matching only the front-end's spelling is what left the QEMU process behind,
# holding the hardware resources the next `start` needs.
#
# `pgrep -f` reads a process's real argv (not a `ps` rendering, which truncates to the
# terminal width), so a long emulator command line is matched in full.
emulator_process_pids() {
    local AVD="${1:-${TARGET_AVD}}"
    local PATTERNS=(
        "emulator.*@${AVD}([[:space:]]|\$)"
        "qemu-system-.*(avd_name=|-avd)[[:space:]=]*${AVD}([[:space:]]|\$)"
    )
    local PIDS="" PID FOUND PATTERN ARGS

    # The pidfile is the most precise handle on the process this script launched, so it is
    # asked first — but only after its command line matches the *same* patterns the scan
    # below uses. A recycled PID is a real hazard here: `emulator.pid` outlives the process
    # it names, so a bare "does the command line mention this AVD" test would happily
    # SIGTERM whatever inherited that PID, a developer's `grep boss_avd_arm64` among them.
    # The test is deliberately the same strict one, so the two paths cannot disagree.
    PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
    if [[ -n "${PID}" ]] && runner_process_alive "${PID}"; then
        ARGS="$(ps -p "${PID}" -o args= 2>/dev/null || true)"
        for PATTERN in "${PATTERNS[@]}"; do
            if [[ -n "${ARGS}" && "${ARGS}" =~ ${PATTERN} ]]; then
                PIDS="${PID}"
                break
            fi
        done
    fi

    for PATTERN in "${PATTERNS[@]}"; do
        FOUND="$(pgrep -f "${PATTERN}" 2>/dev/null || true)"
        for PID in ${FOUND}; do
            [[ " ${PIDS} " == *" ${PID} "* ]] || PIDS="${PIDS} ${PID}"
        done
    done

    printf '%s\n' "${PIDS}"
}

# Bring down the services this runner owns, and leave the AVD process alone.
#
# Two things answer to this runner, and only one of them is the AVD. The Remote ADB Bridge is
# a daemon this script started and can restart in a second; the AVD is machine-wide
# infrastructure whose next boot costs 30-60s and which no single runner owns. So `stop` is
# exactly "stop the bridge" — the shutdown signal this runner can honour — and keeping the
# device is what makes the next `start` instant instead of a cold boot.
#
# There is no flag that stops the AVD, on purpose. A shutdown signal aimed at a Virtual
# Device Session is expensive, irreversible from a script's point of view, and never
# something a routine `stop` or a neighbouring runner should decide on the user's behalf —
# so the user gives it, by hand, when they mean it. One code path and no opt-out is a
# lifecycle that cannot drift into charging every teardown a cold boot.
cmd_stop() {
    echo "🛑 Stopping services for Dedicated AVD '${TARGET_AVD}' (keeping the AVD process)..."

    local SERIAL
    SERIAL="$(get_running_device_serial)"
    local BRIDGE_PID
    BRIDGE_PID="$(get_running_bridge_pid)"

    if [[ -n "${BRIDGE_PID}" ]]; then
        runner_log_stop_request "${LOG_FILE}" "Emulator" "${BRIDGE_PID}" \
            "Remote ADB Bridge for '${TARGET_AVD}' (AVD kept running)"
    fi

    stop_remote_bridge

    if [[ -n "${BRIDGE_PID}" ]]; then
        runner_log_stop_complete "${LOG_FILE}" "Emulator" \
            "Remote ADB Bridge stopped; Virtual Device Session '${TARGET_AVD}' left running"
    fi

    if [[ -n "${SERIAL}" ]]; then
        echo "✅ Dedicated AVD '${TARGET_AVD}' is still running (${SERIAL}) — its process was not stopped."
    else
        echo "ℹ️ No AVD process is currently running for '${TARGET_AVD}'."
    fi
    echo "   To close the AVD process itself, quit the emulator by hand."
}

cmd_start() {
    local FOREGROUND=0
    local DAEMON=0
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --foreground|-f)
                FOREGROUND=1
                shift
                ;;
            -d|--daemon)
                DAEMON=1
                shift
                ;;
            *)
                shift
                ;;
        esac
    done

    # Check if already booted and ready before requiring the emulator binary:
    # when the AVD is already running we only need to re-validate ADB / bridge,
    # which never launches a new emulator. The binary check is only relevant
    # when we are actually about to start one.
    local SERIAL
    SERIAL="$(get_running_device_serial)"
    local RUNNING_PIDS
    RUNNING_PIDS="$(emulator_process_pids)"
    if [[ -n "${SERIAL}" || -n "${RUNNING_PIDS}" ]]; then
        local BOOT_STATUS=""
        if [[ -n "${SERIAL}" ]]; then
            BOOT_STATUS="$(adb_getprop "${SERIAL}" sys.boot_completed)"
        fi
        if [[ "${BOOT_STATUS}" == "1" ]]; then
            ensure_lan_adb_connected
            if [[ ${DAEMON} -eq 1 ]]; then
                echo "ℹ️ Dedicated AVD '${TARGET_AVD}' is already running in background (${SERIAL:-${RUNNING_PIDS}}, PID: $(cat "${PID_FILE}" 2>/dev/null || echo "active"))."
                exit 0
            fi
            attach_logs "${SERIAL}" 1
            exit 0
        fi

        echo "⏳ Dedicated AVD '${TARGET_AVD}' is already running, waiting for boot completion..."
        local BOOTED=0
        for _ in {1..90}; do
            SERIAL="$(get_running_device_serial)"
            if [[ -n "${SERIAL}" ]]; then
                BOOT_STATUS="$(adb_getprop "${SERIAL}" sys.boot_completed)"
                if [[ "${BOOT_STATUS}" == "1" ]]; then
                    BOOTED=1
                    break
                fi
            fi
            sleep 1
        done

        if [[ ${BOOTED} -eq 1 ]]; then
            local EMU_PID
            EMU_PID="$(watched_avd_pid)"
            echo "✅ Dedicated AVD '${TARGET_AVD}' is fully booted and ready (${SERIAL}, PID: ${EMU_PID})!"
            echo "   Log File : ${LOG_FILE}"
            ensure_lan_adb_connected
            if [[ ${DAEMON} -eq 1 ]]; then
                exit 0
            fi
            echo ""
            attach_logs "${SERIAL}" 0
            exit 0
        else
            echo "⚠️ Timeout waiting for '${TARGET_AVD}' to boot. Check logs: ${LOG_FILE}" >&2
            exit 1
        fi
    fi

    if [[ -z "${EMULATOR_BIN}" ]]; then
        echo "❌ Error: Android 'emulator' binary not found." >&2
        echo "💡 Install Android Command Line Tools or configure ANDROID_HOME." >&2
        exit 1
    fi

    if [[ ${FOREGROUND} -eq 1 ]]; then
        echo "🚀 Starting Dedicated AVD '${TARGET_AVD}' in foreground..."
        echo "   Log File : ${LOG_FILE}"
        echo "   Press Ctrl+C to stop."
        echo ""
        exec "${EMULATOR_BIN}" @"${TARGET_AVD}" -no-snapshot-load 2>&1 | tee -a "${LOG_FILE}"
    else
        echo "🚀 Starting Dedicated AVD '${TARGET_AVD}' in background..."
        # Session-isolated: the AVD is machine-wide infrastructure that must survive this
        # script exiting and the terminal being interrupted, so it is started outside the
        # runner's process group rather than merely SIGHUP-proofed.
        local EMU_PID
        EMU_PID="$(detached_spawn \
            "${LOG_FILE}" \
            "${EMULATOR_BIN}" @"${TARGET_AVD}" -no-snapshot-load)"
        echo "${EMU_PID}" > "${PID_FILE}"

        echo "⏳ Waiting for Android system boot completion (AVD: ${TARGET_AVD})..."
        local BOOTED=0
        for _ in {1..90}; do
            SERIAL="$(get_running_device_serial)"
            if [[ -n "${SERIAL}" ]]; then
                local BOOT_STATUS
                BOOT_STATUS="$(adb_getprop "${SERIAL}" sys.boot_completed)"
                if [[ "${BOOT_STATUS}" == "1" ]]; then
                    BOOTED=1
                    break
                fi
            fi
            sleep 1
        done

        if [[ ${BOOTED} -eq 1 ]]; then
            echo "✅ Dedicated AVD '${TARGET_AVD}' is fully booted and ready (${SERIAL}, PID: ${EMU_PID})!"
            echo "   Log File : ${LOG_FILE}"
            ensure_lan_adb_connected
            if [[ ${DAEMON} -eq 1 ]]; then
                exit 0
            fi
            echo ""
            attach_logs "${SERIAL}" 0
            exit 0
        else
            echo "⚠️ Timeout waiting for '${TARGET_AVD}' to boot. Check logs: ${LOG_FILE}" >&2
            exit 1
        fi
    fi
}

# `restart` recycles the dedicated AVD's services without ever stopping the AVD process.
#
# When the AVD is already online and fully booted, there is nothing to stop: the AVD is kept
# and only its Remote ADB Bridge is torn down and rebuilt, because a bridge carried over
# un-restarted is serving a transport whose target process and remote peers have moved on.
# When the AVD is absent, the same bridge teardown runs and `cmd_start` boots a fresh one.
#
# "Restart" is not a no-op on the reuse path, and the two services are what make that true:
# the Virtual Device Session is spared, the Remote ADB Bridge is sent its shutdown signal.
# There is no flag that escalates this into stopping the AVD — a cold boot is the user's call
# (see `cmd_stop`), so this command never has to choose between a cheap restart and a
# decision that is not its to make.
cmd_restart() {
    local ARGS=("$@")

    local SERIAL
    SERIAL="$(get_running_device_serial)"
    local BOOT_STATUS=""
    if [[ -n "${SERIAL}" ]]; then
        BOOT_STATUS="$(adb_getprop "${SERIAL}" sys.boot_completed)"
    fi

    if [[ -n "${SERIAL}" && "${BOOT_STATUS}" == "1" ]]; then
        echo "♻️  Dedicated AVD '${TARGET_AVD}' is already online — reusing existing instance (${SERIAL})."
        echo "   The AVD is kept (no cold boot); restarting its Remote ADB Bridge instead."
        # The AVD is not the only service this runner owns. Leaving the bridge untouched would
        # make this a no-op dressed as a restart: it keeps serving a transport whose target
        # process, LAN endpoint and remote peers have all moved on. So the bridge — the other
        # service — is sent its shutdown signal here, and the `cmd_start` below brings a fresh
        # one up over the AVD that was deliberately never touched.
        stop_remote_bridge
        cmd_start ${ARGS[@]+"${ARGS[@]}"}
        return 0
    fi

    echo "🔄 Restarting Dedicated AVD '${TARGET_AVD}'..."
    runner_log_restart_request "${LOG_FILE}" "Emulator" "Virtual Device Session '${TARGET_AVD}'"

    # Services always come down; the AVD process is not touched. That is the same split
    # `stop` makes, and `restart` is `stop` plus a start.
    local RUNNING_PIDS
    RUNNING_PIDS="$(emulator_process_pids)"
    cmd_stop
    # An AVD that is running but never reported `sys.boot_completed=1` cannot be
    # "restarted" without stopping it, because `cmd_start` only reuses a *ready* device and
    # would otherwise boot a second emulator alongside the first — two processes contending
    # for one device. Since no command here stops the AVD, the user has to: this refuses and
    # says so, rather than booting a rival emulator or quietly doing what the contract forbids.
    if [[ -n "${RUNNING_PIDS}" ]]; then
        echo "⚠️  AVD process for '${TARGET_AVD}' is still running (PIDs: ${RUNNING_PIDS}) but never finished booting."
        echo "   Not starting a second emulator alongside it. Quit the emulator by hand, then run this again."
        return 1
    fi

    # In a subshell: `cmd_start` exits from inside its own boot path, and an `exit` there
    # would otherwise take this confirmation down with it.
    #
    # The status is captured rather than propagated, so a start that failed cannot be
    # recorded below as a Virtual Device Session that came back up. Not written as
    # `( cmd_start ) || return 1`: a subshell used as an operand of `||` has errexit
    # suspended *inside* it, which would let a failed boot path run on past its own guard.
    # Suspension is therefore lifted only for the outer shell, and restored for the subshell
    # where `cmd_start` depends on it to abort.
    set +e
    ( set -e; cmd_start ${ARGS[@]+"${ARGS[@]}"} )
    local START_STATUS=$?
    set -e
    if [[ ${START_STATUS} -ne 0 ]]; then
        return 1
    fi
    runner_log_restart_complete "${LOG_FILE}" "Emulator" \
        "Virtual Device Session '${TARGET_AVD}' back online"
}

ACTION="${1:-start}"
case "${ACTION}" in
    start)
        shift || true
        cmd_start "$@"
        ;;
    stop)
        shift || true
        cmd_stop "$@"
        ;;
    restart)
        shift || true
        cmd_restart "$@"
        ;;
    status)
        shift || true
        cmd_status "$@"
        ;;
    reconnect|repair)
        cmd_reconnect
        ;;
    list|ls)
        cmd_list
        ;;
    logs)
        cmd_logs
        ;;
    *)
        cmd_start "$@"
        ;;
esac
