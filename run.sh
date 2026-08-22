#!/usr/bin/env bash
# ==============================================================================
# Boss Agent Mobile - Unified Master Runner & Service Router
# ==============================================================================
# Master entrypoint for running the automation worker (default) or dispatching
# to any subsystem (web, pb, emu, doctor, live).
#
# Usage:
#   ./run.sh                              # Start Worker daemon (default, with PB & AVD pre-flight gates)
#   ./run.sh worker                       # Start Worker daemon or attach to logs
#   ./run.sh worker stop                  # Stop background Worker daemon
#   ./run.sh worker status                # Check Worker status
#   ./run.sh web                          # Start SvelteKit Web dashboard (./web.sh)
#   ./run.sh pb                           # Manage/Start PocketBase (./pb.sh)
#   ./run.sh pocketbase                   # Manage/Start PocketBase (./pb.sh)
#   ./run.sh emu                          # Manage/Start Dedicated Android AVD (./emulator.sh)
#   ./run.sh emulator                     # Manage/Start Dedicated Android AVD (./emulator.sh)
#   ./run.sh doctor                       # Run system diagnostic health check (./doctor.sh)
#   ./run.sh live [args...]               # Run live mobile test harness
#   ./run.sh --keyword "AI"               # Run live mobile test harness with flags
# ==============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

mkdir -p ".boss_agent"

# Pre-flight environment check
if command -v uv >/dev/null 2>&1; then
    RUNNER=(uv run python3)
elif command -v python3 >/dev/null 2>&1; then
    RUNNER=(python3)
else
    echo "❌ Error: Neither 'uv' nor 'python3' was found in PATH." >&2
    exit 1
fi

# Subcommand dispatch (if matched, delegate directly)
SUBCOMMAND="${1:-}"
case "${SUBCOMMAND}" in
    web|svelte)
        shift
        exec ./web.sh "$@"
        ;;
    pb|pocketbase)
        shift
        exec ./pb.sh "$@"
        ;;
    emu|emulator)
        shift
        exec ./emulator.sh "$@"
        ;;
    doctor|check)
        shift
        exec ./doctor.sh "$@"
        ;;
esac

# ------------------------------------------------------------------------------
# Worker Management Helpers
# ------------------------------------------------------------------------------
WORKER_PID_FILE=".boss_agent/worker.pid"
WORKER_LOG_FILE=".boss_agent/worker.log"

get_running_worker_pid() {
    if [[ -f "${WORKER_PID_FILE}" ]]; then
        local PID
        PID="$(cat "${WORKER_PID_FILE}" 2>/dev/null || true)"
        if [[ -n "${PID}" ]] && ps -p "${PID}" >/dev/null 2>&1; then
            echo "${PID}"
            return 0
        fi
    fi

    local FOUND_PID
    FOUND_PID="$(pgrep -f "scripts/worker.py" 2>/dev/null | head -n 1 || true)"
    if [[ -n "${FOUND_PID}" ]]; then
        echo "${FOUND_PID}" > "${WORKER_PID_FILE}"
        echo "${FOUND_PID}"
        return 0
    fi
    echo ""
}

attach_worker_logs() {
    local PID="$1"
    echo "ℹ️ Automation Worker Daemon is already running (PID: ${PID})."
    echo "👀 Attaching to live log stream (${WORKER_LOG_FILE})... (Press Ctrl+C to detach)"
    echo "----------------------------------------------------------------------"

    trap 'echo -e "\n👋 Detached from Worker logs (Worker daemon is still running in background)."; exit 0' INT TERM

    if [[ ! -f "${WORKER_LOG_FILE}" ]]; then
        touch "${WORKER_LOG_FILE}"
    fi

    exec tail -n 30 -f "${WORKER_LOG_FILE}"
}

# Handle worker subcommands: stop / status
if [[ "${SUBCOMMAND}" == "worker" && "${2:-}" == "stop" ]]; then
    PID="$(get_running_worker_pid)"
    if [[ -n "${PID}" ]]; then
        kill "${PID}" 2>/dev/null || true
        rm -f "${WORKER_PID_FILE}"
        echo "✅ Automation Worker daemon stopped (PID: ${PID})."
    else
        pkill -f "scripts/worker.py" 2>/dev/null || true
        rm -f "${WORKER_PID_FILE}"
        echo "ℹ️ No running Worker daemon found."
    fi
    exit 0
fi

if [[ "${SUBCOMMAND}" == "worker" && "${2:-}" == "status" ]]; then
    PID="$(get_running_worker_pid)"
    if [[ -n "${PID}" ]]; then
        echo "🟢 Automation Worker daemon is RUNNING (PID: ${PID})."
        echo "   Log File: ${WORKER_LOG_FILE}"
        exit 0
    else
        echo "🔴 Automation Worker daemon is NOT RUNNING."
        exit 1
    fi
fi

# ------------------------------------------------------------------------------
# Pre-Flight Verification Gates (Required for Worker and Live Harness)
# ------------------------------------------------------------------------------

# 1. Resolve and check PocketBase health
POCKETBASE_URL="${POCKETBASE_URL:-http://127.0.0.1:8090}"
check_pocketbase_health() {
    local HEALTH_URL="${POCKETBASE_URL%/}/api/health"
    if ! curl -s -f "${HEALTH_URL}" >/dev/null 2>&1; then
        echo "❌ Error: PocketBase State Stream is not reachable at ${HEALTH_URL}" >&2
        echo "" >&2
        echo "💡 PocketBase State Stream broker must be running first:" >&2
        echo "   - Local PocketBase: run './pb.sh' or './run.sh pb' in another terminal" >&2
        echo "   - Remote PocketBase: export POCKETBASE_URL=\"http://<remote-ip>:<port>\"" >&2
        echo "" >&2
        exit 1
    fi
}

# 2. Resolve and check dedicated Android AVD status
TARGET_AVD="${ANDROID_AVD:-${AVD_NAME:-}}"
if [[ -z "${TARGET_AVD}" && -f "config/settings.local.yaml" ]]; then
    TARGET_AVD="$(grep -E "^[[:space:]]*avd_name:" config/settings.local.yaml 2>/dev/null | awk '{print $2}' | tr -d '"' | tr -d "'" || true)"
fi
TARGET_AVD="${TARGET_AVD:-boss_avd_arm64}"

check_dedicated_avd_ready() {
    if ! command -v adb >/dev/null 2>&1; then
        echo "❌ Error: 'adb' command not found in PATH." >&2
        echo "💡 Install Android Platform Tools: brew install android-platform-tools" >&2
        exit 1
    fi

    local RUNNING_AVD_SERIAL=""
    local DEV_LIST
    DEV_LIST="$(adb devices 2>/dev/null | grep -E "emulator-[0-9]+" | awk '{print $1}' || true)"
    for dev in ${DEV_LIST}; do
        local AVD_FOUND
        AVD_FOUND="$(adb -s "${dev}" emu avd name 2>/dev/null | head -n 1 | tr -d '\r\n' || true)"
        if [[ "${AVD_FOUND}" == "${TARGET_AVD}" ]]; then
            RUNNING_AVD_SERIAL="${dev}"
            break
        fi
    done

    if [[ -z "${RUNNING_AVD_SERIAL}" ]]; then
        echo "❌ Error: Dedicated Android AVD '${TARGET_AVD}' is not running." >&2
        echo "" >&2
        echo "💡 The automation worker requires the dedicated '${TARGET_AVD}' emulator:" >&2
        echo "   - Start dedicated AVD: run './emulator.sh' or './run.sh emu'" >&2
        echo "   - Check AVD list:      run './emulator.sh list'" >&2
        echo "" >&2
        exit 1
    fi

    local BOOT_STATUS
    BOOT_STATUS="$(adb -s "${RUNNING_AVD_SERIAL}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r\n' || true)"
    if [[ "${BOOT_STATUS}" != "1" ]]; then
        echo "❌ Error: Dedicated Android AVD '${TARGET_AVD}' (${RUNNING_AVD_SERIAL}) is still booting (sys.boot_completed='${BOOT_STATUS}')." >&2
        echo "💡 Please wait a few seconds for the emulator to finish booting, then retry." >&2
        exit 1
    fi
}

# Run Pre-flight Gates
check_pocketbase_health
check_dedicated_avd_ready

# Route to Worker or Live Harness
if [[ $# -eq 0 || "${1:-}" == "worker" || "${1:-}" == "--worker" ]]; then
    if [[ "${1:-}" == "worker" || "${1:-}" == "--worker" ]]; then
        shift || true
    fi

    # Check if worker is already running locally
    RUNNING_PID="$(get_running_worker_pid)"
    if [[ -n "${RUNNING_PID}" ]]; then
        attach_worker_logs "${RUNNING_PID}"
    fi

    echo "🤖 Starting Boss Agent Mobile Automation Worker Daemon..."
    echo "   PocketBase Broker : ${POCKETBASE_URL}"
    echo "   Dedicated AVD     : ${TARGET_AVD}"
    echo "   Log File          : ${WORKER_LOG_FILE}"
    echo "   Press Ctrl+C to stop."
    echo ""

    # Start worker and pipe output
    "${RUNNER[@]}" scripts/worker.py "$@" >> "${WORKER_LOG_FILE}" 2>&1 &
    PID=$!
    echo "${PID}" > "${WORKER_PID_FILE}"

    trap 'echo -e "\n🛑 Stopping Worker daemon (PID: '"${PID}"')..."; kill '"${PID}"' 2>/dev/null || true; rm -f '"${WORKER_PID_FILE}"'; exit 0' INT TERM

    tail -n 0 -f "${WORKER_LOG_FILE}"
fi

if [[ "${1:-}" == "live" ]]; then
    shift || true
fi

echo "🚀 Launching Boss Agent Mobile Live Harness..."
echo "   PocketBase Broker : ${POCKETBASE_URL}"
echo "   Dedicated AVD     : ${TARGET_AVD}"
exec "${RUNNER[@]}" scripts/run_live_test.py "$@"
