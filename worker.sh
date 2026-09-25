#!/usr/bin/env bash
# ==============================================================================
# Boss Agent Mobile - Dedicated Automation Worker Runner
# ==============================================================================
# Starts, stops, restarts, or checks status of the Automation Worker daemon with
# persistent logging, pre-flight gates, and auto-attach if already running.
#
# Usage:
#   ./worker.sh                   # Start or attach to Worker daemon
#   ./worker.sh start             # Start or attach to Worker daemon
#   ./worker.sh start --daemon    # Start Worker daemon in background
#   ./worker.sh stop              # Stop running Worker daemon
#   ./worker.sh restart           # Restart Worker daemon in foreground
#   ./worker.sh restart --daemon  # Restart Worker daemon in background
#   ./worker.sh status            # Check Worker daemon status
#   ./wk.sh <cmd>                 # Symlink alias for ./worker.sh
# ==============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

# Shared process-lifecycle primitives and the one config read.
# shellcheck source=runner_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/runner_lib.sh"

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

WORKER_PID_FILE=".boss_agent/worker.pid"
WORKER_LOG_FILE=".boss_agent/worker.log"
WORKER_STOP_TIMEOUT_SEC="${WORKER_STOP_TIMEOUT_SEC:-10}"

# The library's primitives, under this script's historical names. They were identical
# copies of web.sh's — including the zombie guard — which is exactly the drift the
# library exists to end.
process_alive() {
    runner_process_alive "${1:-}"
}

wait_until() {
    runner_wait_until "$@"
}

process_gone() {
    runner_process_gone "${1:-}"
}

get_running_worker_pid() {
    if [[ -f "${WORKER_PID_FILE}" ]]; then
        local PID
        PID="$(cat "${WORKER_PID_FILE}" 2>/dev/null || true)"
        if [[ -n "${PID}" ]] && process_alive "${PID}"; then
            echo "${PID}"
            return 0
        fi
    fi

    # Fallback to process search across worktrees
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

cmd_status() {
    echo "🔍 Checking Automation Worker status..."
    local PID
    PID="$(get_running_worker_pid)"

    if [[ -n "${PID}" ]] && process_alive "${PID}"; then
        echo "🟢 Automation Worker daemon is RUNNING (PID: ${PID})."
        echo "   Log File: ${WORKER_LOG_FILE}"
        return 0
    else
        echo "🔴 Automation Worker daemon is NOT RUNNING."
        rm -f "${WORKER_PID_FILE}"
        return 1
    fi
}

cmd_stop() {
    echo "🛑 Stopping Automation Worker daemon..."
    local STOPPED=0
    local PID
    PID="$(get_running_worker_pid)"

    if [[ -n "${PID}" ]]; then
        kill "${PID}" 2>/dev/null || true
        pkill -P "${PID}" 2>/dev/null || true
        if ! wait_until "${WORKER_STOP_TIMEOUT_SEC}" process_gone "${PID}"; then
            echo "⚠️ Graceful shutdown timed out after ${WORKER_STOP_TIMEOUT_SEC}s; sending SIGKILL."
            kill -9 "${PID}" 2>/dev/null || true
            wait_until 2 process_gone "${PID}" || true
        fi
        STOPPED=1
    fi

    # Reclaim from any lingering scripts/worker.py (e.g. from another worktree)
    local RESIDUAL_PIDS
    RESIDUAL_PIDS="$(pgrep -f "scripts/worker.py" 2>/dev/null || true)"
    if [[ -n "${RESIDUAL_PIDS}" ]]; then
        for r_pid in ${RESIDUAL_PIDS}; do
            kill "${r_pid}" 2>/dev/null || true
            if ! wait_until 2 process_gone "${r_pid}"; then
                kill -9 "${r_pid}" 2>/dev/null || true
            fi
            STOPPED=1
        done
    fi

    rm -f "${WORKER_PID_FILE}"

    if [[ ${STOPPED} -eq 1 ]]; then
        echo "✅ Automation Worker daemon stopped."
    else
        echo "ℹ️ No running Worker daemon found."
    fi
}

# ------------------------------------------------------------------------------
# Pre-Flight Verification Gates
# ------------------------------------------------------------------------------
check_pocketbase_health() {
    # One config read, through the library: the CLI resolves the precedence chain
    # and the environment, and the single grep fallback covers a copied script root.
    POCKETBASE_URL="${POCKETBASE_URL:-$(runner_config_value pocketbase_url http://127.0.0.1:8090 pb_url)}"
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

check_dedicated_avd_ready() {
    TARGET_AVD="${ANDROID_AVD:-${AVD_NAME:-$(runner_config_value avd_name boss_avd_arm64)}}"

    local STATUS_OUTPUT=""
    if STATUS_OUTPUT="$(ANDROID_AVD="${TARGET_AVD}" ./emulator.sh status 2>&1)"; then
        return 0
    fi

    echo "${STATUS_OUTPUT}" >&2
    echo "" >&2
    echo "❌ Error: Dedicated Android AVD '${TARGET_AVD}' is not ready for the Automation Worker." >&2
    echo "" >&2
    echo "💡 The automation worker requires the dedicated '${TARGET_AVD}' emulator:" >&2
    echo "   - Start dedicated AVD: run './emulator.sh' or './run.sh emu'" >&2
    echo "   - Check AVD list:      run './emulator.sh list'" >&2
    echo "   - Stuck 'offline'?     restart it: './emulator.sh stop && ./emulator.sh'" >&2
    echo "" >&2
    exit 1
}

check_appium_health() {
    APPIUM_URL="${APPIUM_URL:-$(runner_config_value server_url http://127.0.0.1:4723 appium_url)}"
    local CHECK_URL="${APPIUM_URL%/}"
    CHECK_URL="${CHECK_URL/0.0.0.0/127.0.0.1}"
    local STATUS_URL="${CHECK_URL}/status"

    if ! curl -s -f "${STATUS_URL}" >/dev/null 2>&1; then
        echo "❌ Error: Appium server is not reachable at ${APPIUM_URL}" >&2
        echo "" >&2
        echo "💡 The automation worker requires Appium server to drive the Android device:" >&2
        echo "   - Start Appium: run './appium.sh' or './run.sh appium' (or './appium.sh start --daemon')" >&2
        echo "   - Check status: run './appium.sh status'" >&2
        echo "" >&2
        exit 1
    fi
}

cmd_start() {
    local RUNNING_PID
    RUNNING_PID="$(get_running_worker_pid)"
    if [[ -n "${RUNNING_PID}" ]] && process_alive "${RUNNING_PID}"; then
        attach_worker_logs "${RUNNING_PID}"
    fi

    # Run Pre-flight Gates
    check_pocketbase_health
    check_dedicated_avd_ready
    check_appium_health

    local IS_DAEMON=0
    local WORKER_ARGS=()
    for arg in "$@"; do
        if [[ "${arg}" == "--daemon" ]]; then
            IS_DAEMON=1
        else
            WORKER_ARGS+=("${arg}")
        fi
    done

    echo "🤖 Starting Boss Agent Mobile Automation Worker Daemon..."
    echo "   PocketBase Broker : ${POCKETBASE_URL:-http://127.0.0.1:8090}"
    echo "   Dedicated AVD     : ${TARGET_AVD:-boss_avd_arm64}"
    echo "   Log File          : ${WORKER_LOG_FILE}"

    if [[ "${IS_DAEMON}" -eq 1 || "${DAEMON:-0}" -eq 1 ]]; then
        nohup "${RUNNER[@]}" scripts/worker.py "${WORKER_ARGS[@]}" >> "${WORKER_LOG_FILE}" 2>&1 &
        local PID=$!
        echo "${PID}" > "${WORKER_PID_FILE}"
        echo "✅ Automation Worker daemon started in background (PID: ${PID})."
        echo "   Logs: ${WORKER_LOG_FILE}"
        return 0
    fi

    echo "   Press Ctrl+C to stop."
    echo ""

    "${RUNNER[@]}" scripts/worker.py "${WORKER_ARGS[@]}" >> "${WORKER_LOG_FILE}" 2>&1 &
    local PID=$!
    echo "${PID}" > "${WORKER_PID_FILE}"

    trap 'echo -e "\n🛑 Stopping Worker daemon (PID: '"${PID}"')..."; kill '"${PID}"' 2>/dev/null || true; rm -f '"${WORKER_PID_FILE}"'; exit 0' INT TERM

    tail -n 0 -f "${WORKER_LOG_FILE}"
}

cmd_restart() {
    echo "🔄 Restarting Automation Worker daemon..."
    cmd_stop || true
    sleep 0.5
    cmd_start "$@"
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
    restart)
        shift || true
        cmd_restart "$@"
        ;;
    status)
        cmd_status
        ;;
    *)
        cmd_start "$@"
        ;;
esac
