#!/usr/bin/env bash
# ==============================================================================
# Boss Agent Mobile - Dedicated Android Virtual Device (AVD) Runner
# ==============================================================================
# Manages the dedicated Android Virtual Device with boot synchronization.
#
# Usage:
#   ./emulator.sh                     # Start dedicated AVD in background and wait for boot
#   ./emulator.sh start               # Start dedicated AVD in background and wait for boot
#   ./emulator.sh start --foreground  # Start dedicated AVD in foreground
#   ./emulator.sh status              # Check if dedicated AVD is online and booted
#   ./emulator.sh list                # List all installed local AVDs
#   ./emulator.sh stop                # Stop the running dedicated AVD
# ==============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

mkdir -p ".boss_agent"

# Locate Android emulator binary
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

# Locate adb binary
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

# Resolve target AVD name (CLI flag -> ENV -> config -> default)
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

    # Read from config/settings.local.yaml or config/settings.example.yaml
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

    # Check if boss_avd_arm64 exists in list
    if [[ -n "${EMULATOR_BIN}" ]]; then
        local LIST_AVDS
        LIST_AVDS="$("${EMULATOR_BIN}" -list-avds 2>/dev/null || true)"
        if echo "${LIST_AVDS}" | grep -q "^boss_avd_arm64$"; then
            echo "boss_avd_arm64"
            return 0
        fi
        # If boss_avd_arm64 not found, fallback to first available AVD
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

# Parse optional global flags
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

# Find serial of device running TARGET_AVD (e.g. emulator-5554)
get_running_device_serial() {
    if [[ -z "${ADB_BIN}" ]]; then
        echo ""
        return 0
    fi

    local DEV_LIST
    DEV_LIST="$("${ADB_BIN}" devices 2>/dev/null | grep -E "emulator-[0-9]+" | awk '{print $1}' || true)"

    for dev in ${DEV_LIST}; do
        local AVD_NAME_FOUND
        AVD_NAME_FOUND="$("${ADB_BIN}" -s "${dev}" emu avd name 2>/dev/null | head -n 1 | tr -d '\r\n' || true)"
        if [[ "${AVD_NAME_FOUND}" == "${TARGET_AVD}" ]]; then
            echo "${dev}"
            return 0
        fi
    done
    echo ""
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

    # Check boot completion status
    local BOOT_STATUS
    BOOT_STATUS="$("${ADB_BIN}" -s "${SERIAL}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r\n' || true)"

    if [[ "${BOOT_STATUS}" == "1" ]]; then
        echo "🟢 Dedicated AVD '${TARGET_AVD}' is ONLINE and READY (${SERIAL})."
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

    if [[ -n "${SERIAL}" && -n "${ADB_BIN}" ]]; then
        "${ADB_BIN}" -s "${SERIAL}" emu kill 2>/dev/null || true
        echo "✅ Sent emu kill to ${SERIAL} (${TARGET_AVD})."
    else
        # Fallback process kill
        pkill -f "emulator.*@${TARGET_AVD}" 2>/dev/null || true
        echo "ℹ️ Stopped emulator processes for ${TARGET_AVD}."
    fi
}

cmd_start() {
    local FOREGROUND=0
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --foreground|-f)
                FOREGROUND=1
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
        BOOT_STATUS="$("${ADB_BIN}" -s "${SERIAL}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r\n' || true)"
        if [[ "${BOOT_STATUS}" == "1" ]]; then
            echo "ℹ️ Dedicated AVD '${TARGET_AVD}' is already online and ready (${SERIAL})."
            exit 0
        fi
    fi

    local LOG_FILE=".boss_agent/emulator.log"

    if [[ ${FOREGROUND} -eq 1 ]]; then
        echo "🚀 Starting Dedicated AVD '${TARGET_AVD}' in foreground..."
        exec "${EMULATOR_BIN}" @"${TARGET_AVD}" -no-snapshot-load
    else
        echo "🚀 Starting Dedicated AVD '${TARGET_AVD}' in background..."
        "${EMULATOR_BIN}" @"${TARGET_AVD}" -no-snapshot-load > "${LOG_FILE}" 2>&1 &
        local EMU_PID=$!
        echo "${EMU_PID}" > ".boss_agent/emulator.pid"

        echo "⏳ Waiting for Android system boot completion (AVD: ${TARGET_AVD})..."
        local BOOTED=0
        for i in {1..90}; do
            SERIAL="$(get_running_device_serial)"
            if [[ -n "${SERIAL}" && -n "${ADB_BIN}" ]]; then
                local BOOT_STATUS
                BOOT_STATUS="$("${ADB_BIN}" -s "${SERIAL}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r\n' || true)"
                if [[ "${BOOT_STATUS}" == "1" ]]; then
                    BOOTED=1
                    break
                fi
            fi
            sleep 1
        done

        if [[ ${BOOTED} -eq 1 ]]; then
            echo "✅ Dedicated AVD '${TARGET_AVD}' is fully booted and ready (${SERIAL}, PID: ${EMU_PID})!"
            exit 0
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
    *)
        cmd_start "$@"
        ;;
esac
