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
#   ./worker.sh attach            # Attach to live Worker daemon log stream
#   ./worker.sh logs              # Alias for attach
#   ./run.sh worker <cmd>           # Short orchestrator route for ./worker.sh
#
# There is no root-level `wk.sh` alias script: a short alias in the project root collided
# with the full name under shell tab-completion, and `./run.sh worker` reaches the same runner.
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

# The daemon's stdout is a redirected file, and a redirected stdout is block-buffered by
# default. The Automation Worker logs its shutdown acknowledgment the instant it accepts
# SIGTERM, and the E2E Pre-Test Teardown Gate reads that exact line back out of worker.log
# to prove a clean stop — so unbuffered output is the difference between a gate that sees the
# acknowledgment and one that fails it, or hides a real "exited without logging shutdown
# feedback" behind a flake. Injected on the launch line rather than exported, so an
# inherited PYTHONUNBUFFERED=0 in the operator's shell cannot re-buffer it.
WORKER_DAEMON=(env PYTHONUNBUFFERED=1 "${RUNNER[@]}")

WORKER_PID_FILE=".boss_agent/worker.pid"
WORKER_LOG_FILE=".boss_agent/worker.log"
WORKER_STOP_TIMEOUT_SEC="${WORKER_STOP_TIMEOUT_SEC:-10}"


get_running_worker_pid() {
    if [[ -f "${WORKER_PID_FILE}" ]]; then
        local PID
        PID="$(cat "${WORKER_PID_FILE}" 2>/dev/null || true)"
        if [[ -n "${PID}" ]] && runner_process_alive "${PID}" && runner_process_cwd_alive "${PID}"; then
            echo "${PID}"
            return 0
        fi
    fi

    # Fallback to process search across worktrees
    local FOUND_PID
    while IFS= read -r FOUND_PID; do
        [[ -z "${FOUND_PID}" ]] && continue
        if runner_process_alive "${FOUND_PID}" && runner_process_cwd_alive "${FOUND_PID}"; then
            echo "${FOUND_PID}" > "${WORKER_PID_FILE}"
            echo "${FOUND_PID}"
            return 0
        fi
    done < <(pgrep -f "scripts/worker.py" 2>/dev/null || true)
    echo ""
}

cmd_attach() {
    local PID
    PID="$(get_running_worker_pid)"
    if [[ -z "${PID}" ]] || ! runner_process_alive "${PID}"; then
        echo "🔴 Automation Worker daemon is NOT RUNNING." >&2
        echo "   Cannot attach to log stream. Start worker first via: ./worker.sh" >&2
        return 1
    fi
    runner_attached_logs "${PID}" "${WORKER_LOG_FILE}" "Automation Worker daemon"
}


cmd_status() {
    echo "🔍 Checking Automation Worker status..."
    local PID
    PID="$(get_running_worker_pid)"

    if [[ -n "${PID}" ]] && runner_process_alive "${PID}"; then
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
        runner_log_stop_request "${WORKER_LOG_FILE}" "Worker" "${PID}" "Automation Worker daemon"
        # Children first: the recorded PID is the launcher, and the Automation Worker holding
        # the Virtual Device Session is its child. Signalling the parent first re-parents the
        # child to init, where it can no longer be found or stopped at all.
        pkill -P "${PID}" 2>/dev/null || true
        runner_graceful_stop "${PID}" "${WORKER_STOP_TIMEOUT_SEC}" "Automation Worker" "${WORKER_LOG_FILE}"
        STOPPED=1
    fi

    # Reclaim from any lingering scripts/worker.py (e.g. from another worktree)
    local RESIDUAL_PIDS
    RESIDUAL_PIDS="$(pgrep -f "scripts/worker.py" 2>/dev/null || true)"
    if [[ -n "${RESIDUAL_PIDS}" ]]; then
        for r_pid in ${RESIDUAL_PIDS}; do
            runner_graceful_stop "${r_pid}" 2 "Automation Worker" "${WORKER_LOG_FILE}"
            STOPPED=1
        done
    fi

    rm -f "${WORKER_PID_FILE}"

    if [[ ${STOPPED} -eq 1 ]]; then
        runner_log_stop_complete "${WORKER_LOG_FILE}" "Worker" "Automation Worker daemon released the Virtual Device Session"
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
        echo "   - Local PocketBase: run './pocketbase.sh' or './run.sh pb' in another terminal" >&2
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
    local IS_DAEMON=0
    local WORKER_ARGS=()
    for arg in "$@"; do
        if [[ "${arg}" == "--daemon" ]]; then
            IS_DAEMON=1
        else
            WORKER_ARGS+=("${arg}")
        fi
    done

    local RUNNING_PID
    RUNNING_PID="$(get_running_worker_pid)"
    if [[ -n "${RUNNING_PID}" ]] && runner_process_alive "${RUNNING_PID}"; then
        if [[ "${IS_DAEMON}" -eq 1 || "${DAEMON:-0}" -eq 1 ]]; then
            echo "ℹ️ Automation Worker daemon is already running in background (PID: ${RUNNING_PID})."
            echo "   Logs: ${WORKER_LOG_FILE}"
            return 0
        else
            runner_attached_logs "${RUNNING_PID}" "${WORKER_LOG_FILE}" "Automation Worker daemon"
            return 0
        fi
    fi

    # Run Pre-flight Gates
    check_pocketbase_health
    check_dedicated_avd_ready
    check_appium_health

    echo "🤖 Starting Boss Agent Mobile Automation Worker Daemon..."
    echo "   PocketBase Broker : ${POCKETBASE_URL:-http://127.0.0.1:8090}"
    echo "   Dedicated AVD     : ${TARGET_AVD:-boss_avd_arm64}"
    echo "   Log File          : ${WORKER_LOG_FILE}"

    if [[ "${IS_DAEMON}" -eq 1 || "${DAEMON:-0}" -eq 1 ]]; then
        if [[ ${#WORKER_ARGS[@]} -gt 0 ]]; then
            nohup "${WORKER_DAEMON[@]}" scripts/worker.py "${WORKER_ARGS[@]}" >> "${WORKER_LOG_FILE}" 2>&1 &
        else
            nohup "${WORKER_DAEMON[@]}" scripts/worker.py >> "${WORKER_LOG_FILE}" 2>&1 &
        fi
        local PID=$!
        echo "${PID}" > "${WORKER_PID_FILE}"
        echo "✅ Automation Worker daemon started in background (PID: ${PID})."
        echo "   Logs: ${WORKER_LOG_FILE}"
        return 0
    fi

    echo "   Press Ctrl+C to stop."
    echo ""

    if [[ ${#WORKER_ARGS[@]} -gt 0 ]]; then
        "${WORKER_DAEMON[@]}" scripts/worker.py "${WORKER_ARGS[@]}" >> "${WORKER_LOG_FILE}" 2>&1 &
    else
        "${WORKER_DAEMON[@]}" scripts/worker.py >> "${WORKER_LOG_FILE}" 2>&1 &
    fi
    local PID=$!
    echo "${PID}" > "${WORKER_PID_FILE}"

    trap 'echo -e "\n🛑 Stopping Worker daemon (PID: '"${PID}"')..."; kill '"${PID}"' 2>/dev/null || true; rm -f '"${WORKER_PID_FILE}"'; exit 0' INT TERM

    tail -n 0 -f "${WORKER_LOG_FILE}"
}

cmd_restart() {
    echo "🔄 Restarting Automation Worker daemon..."
    runner_log_restart_request "${WORKER_LOG_FILE}" "Worker" "Automation Worker daemon"
    cmd_stop || true
    sleep 0.5
    cmd_start "$@"
    # Only reached when `cmd_start` returns rather than attaching the live log stream; in
    # daemon mode that is the point at which the replacement daemon is genuinely up.
    runner_log_restart_complete "${WORKER_LOG_FILE}" "Worker" "Automation Worker daemon back online"
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
    attach|logs)
        cmd_attach
        ;;
    *)
        cmd_start "$@"
        ;;
esac

