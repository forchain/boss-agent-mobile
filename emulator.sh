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
#   ./emulator.sh list                # List all installed local AVDs
#   ./emulator.sh logs                # Attach to live log stream of running AVD
#   ./emulator.sh stop                # Stop the running dedicated AVD
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

mkdir -p ".boss_agent"

PID_FILE=".boss_agent/emulator.pid"
LOG_FILE=".boss_agent/emulator.log"

find_emulator_binary() {
    if command -v emulator >/dev/null 2>&1; then
        echo "emulator"
    elif [[ -n "${ANDROID_HOME:-}" && -x "${ANDROID_HOME}/emulator/emulator" ]]; then
        echo "${ANDROID_HOME}/emulator/emulator"
    elif [[ -n "${ANDROID_SDK_ROOT:-}" && -x "${ANDROID_SDK_ROOT}/emulator/emulator" ]]; then
        echo "${ANDROID_SDK_ROOT}/emulator/emulator"
    elif [[ -x "$HOME/Library/Android/sdk/emulator/emulator" ]]; then
        echo "$HOME/Library/Android/sdk/emulator/emulator"
    else
        echo ""
    fi
}

find_adb_binary() {
    if command -v adb >/dev/null 2>&1; then
        echo "adb"
    elif [[ -n "${ANDROID_HOME:-}" && -x "${ANDROID_HOME}/platform-tools/adb" ]]; then
        echo "${ANDROID_HOME}/platform-tools/adb"
    elif [[ -n "${ANDROID_SDK_ROOT:-}" && -x "${ANDROID_SDK_ROOT}/platform-tools/adb" ]]; then
        echo "${ANDROID_SDK_ROOT}/platform-tools/adb"
    elif [[ -x "$HOME/Library/Android/sdk/platform-tools/adb" ]]; then
        echo "$HOME/Library/Android/sdk/platform-tools/adb"
    else
        echo ""
    fi
}

EMULATOR_BIN="$(find_emulator_binary)"
ADB_BIN="$(find_adb_binary)"

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
# the command had to be killed). macOS ships no coreutils `timeout`, so the bound is
# enforced by a watchdog that SIGTERMs - then SIGKILLs - the child. Pass a simple external
# command only: a pipeline would leave its earlier stages running past the bound.
bounded_run() {
    local TIMEOUT_SEC="$1"
    shift
    local OUT_FILE
    OUT_FILE="$(mktemp "${TMPDIR:-/tmp}/boss_agent_bounded.XXXXXX")"

    "$@" >"${OUT_FILE}" 2>/dev/null &
    local CMD_PID=$!

    # The watchdog must not inherit this process's stdout: when `bounded_run` is used in a
    # command substitution, a surviving sleeper holding the pipe would keep the caller
    # waiting until it wakes up.
    (
        sleep "${TIMEOUT_SEC}"
        if kill -0 "${CMD_PID}" 2>/dev/null; then
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
    rm -f "${OUT_FILE}"
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
    local RAW
    RAW="$(adb_query -s "${SERIAL}" emu avd name || true)"
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

    if [[ -f "config/settings.local.yaml" ]]; then
        local CONF_AVD
        CONF_AVD="$(grep -E "^[[:space:]]*avd_name:" config/settings.local.yaml 2>/dev/null | awk '{print $2}' | tr -d '"' | tr -d "'" || true)"
        if [[ -n "${CONF_AVD}" ]]; then
            echo "${CONF_AVD}"
            return 0
        fi
    fi

    if [[ -f "config/settings.example.yaml" ]]; then
        local CONF_AVD
        CONF_AVD="$(grep -E "^[[:space:]]*avd_name:" config/settings.example.yaml 2>/dev/null | awk '{print $2}' | tr -d '"' | tr -d "'" || true)"
        if [[ -n "${CONF_AVD}" ]]; then
            echo "${CONF_AVD}"
            return 0
        fi
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
    DEV_LIST="$(printf '%s\n' "${RAW_DEVICES}" \
        | awk '$1 ~ /^emulator-[0-9]+$/ && $2 == "device" { print $1 }')"

    # Every candidate is probed, however slow: giving up on the scan early could miss the
    # dedicated AVD sitting behind unresponsive siblings. The per-query bound, not a global
    # budget, is what keeps this responsive.
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

attach_logs() {
    local SERIAL="$1"
    local ALREADY_RUNNING="${2:-0}"
    local PID
    PID="$(cat "${PID_FILE}" 2>/dev/null || echo "active")"

    if [[ "${ALREADY_RUNNING}" == "1" ]]; then
        echo "ℹ️ Dedicated AVD '${TARGET_AVD}' is already running (${SERIAL}, PID: ${PID})"
    fi
    echo "👀 Attaching to live log stream (${LOG_FILE})... (Press Ctrl+C to detach)"
    echo "----------------------------------------------------------------------"

    trap 'echo -e "\n👋 Detached from emulator logs (AVD is still running in background)."; exit 0' INT TERM

    if [[ ! -f "${LOG_FILE}" ]]; then
        touch "${LOG_FILE}"
    fi

    exec tail -n 30 -f "${LOG_FILE}"
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

    if [[ "${BOOT_STATUS}" == "1" ]]; then
        echo "🟢 Dedicated AVD '${TARGET_AVD}' is ONLINE and READY (${SERIAL})."
        echo "   Log file : ${LOG_FILE}"
        return 0
    else
        echo "🟡 Dedicated AVD '${TARGET_AVD}' is BOOTING (${SERIAL}, sys.boot_completed='${BOOT_STATUS}')."
        return 1
    fi
}

cmd_stop() {
    echo "🛑 Stopping Dedicated AVD '${TARGET_AVD}'..."
    local SERIAL
    SERIAL="$(get_running_device_serial)"

    if [[ -n "${SERIAL}" ]] && adb_query -s "${SERIAL}" emu kill >/dev/null; then
        echo "✅ Sent emu kill to ${SERIAL} (${TARGET_AVD})."
    else
        if [[ -n "${SERIAL}" ]]; then
            # A kill the wedged device never acknowledged leaves the emulator running, so
            # fall back to the same process cleanup the "no device found" path uses.
            echo "⚠️ ${SERIAL} did not acknowledge the kill within ${ADB_QUERY_TIMEOUT_SEC}s."
        fi
        pkill -f "emulator.*@${TARGET_AVD}" 2>/dev/null || true
        echo "ℹ️ Stopped emulator processes for ${TARGET_AVD}."
    fi
    rm -f "${PID_FILE}"
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

    if [[ -z "${EMULATOR_BIN}" ]]; then
        echo "❌ Error: Android 'emulator' binary not found." >&2
        echo "💡 Install Android Command Line Tools or configure ANDROID_HOME." >&2
        exit 1
    fi

    # Check if already booted and ready
    local SERIAL
    SERIAL="$(get_running_device_serial)"
    if [[ -n "${SERIAL}" ]]; then
        local BOOT_STATUS
        BOOT_STATUS="$(adb_getprop "${SERIAL}" sys.boot_completed)"
        if [[ "${BOOT_STATUS}" == "1" ]]; then
            if [[ ${DAEMON} -eq 1 ]]; then
                echo "ℹ️ Dedicated AVD '${TARGET_AVD}' is already running in background (${SERIAL}, PID: $(cat "${PID_FILE}" 2>/dev/null || echo "active"))."
                exit 0
            fi
            attach_logs "${SERIAL}" 1
        fi
    fi

    if [[ ${FOREGROUND} -eq 1 ]]; then
        echo "🚀 Starting Dedicated AVD '${TARGET_AVD}' in foreground..."
        echo "   Log File : ${LOG_FILE}"
        echo "   Press Ctrl+C to stop."
        echo ""
        exec "${EMULATOR_BIN}" @"${TARGET_AVD}" -no-snapshot-load 2>&1 | tee -a "${LOG_FILE}"
    else
        echo "🚀 Starting Dedicated AVD '${TARGET_AVD}' in background..."
        "${EMULATOR_BIN}" @"${TARGET_AVD}" -no-snapshot-load >> "${LOG_FILE}" 2>&1 &
        local EMU_PID=$!
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
            if [[ ${DAEMON} -eq 1 ]]; then
                exit 0
            fi
            echo ""
            attach_logs "${SERIAL}" 0
        else
            echo "⚠️ Timeout waiting for '${TARGET_AVD}' to boot. Check logs: ${LOG_FILE}" >&2
            exit 1
        fi
    fi
}

ACTION="${1:-start}"
case "${ACTION}" in
    start)
        shift || true
        cmd_start "$@"
        ;;
    stop)
        cmd_stop
        ;;
    status)
        cmd_status
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
