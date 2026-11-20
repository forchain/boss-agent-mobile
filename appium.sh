#!/usr/bin/env bash
# ==============================================================================
# Boss Agent Mobile - Appium Server Standalone Runner
# ==============================================================================
# Manages local Appium automation server with persistent logging and
# auto-attach to live log stream if already running.
#
# Usage:
#   ./appium.sh                   # Start or attach to Appium in foreground
#   ./appium.sh start             # Start or attach to Appium in foreground
#   ./appium.sh start --daemon    # Start Appium in background
#   ./appium.sh stop              # Stop running background Appium
#   ./appium.sh restart           # Restart Appium server
#   ./appium.sh status            # Check Appium health and status
# ==============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

# Shared process-lifecycle primitives. Sourced rather than copied: this script's stop
# path used to resolve "who owns the port" with a bare `lsof -ti` and then SIGKILL it
# 0.5s after SIGTERM — a connected client could be killed, and a cooperative Appium
# never got its budget.
# shellcheck source=runner_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/runner_lib.sh"

# Seconds to wait for a cooperative exit before escalating to SIGKILL.
APPIUM_STOP_TIMEOUT_SEC="${APPIUM_STOP_TIMEOUT_SEC:-10}"

mkdir -p ".boss_agent"

PID_FILE=".boss_agent/appium.pid"
LOG_FILE=".boss_agent/appium.log"

resolve_config_url() {
    # One read, through the library. This used to be a Python probe with its own
    # `sys.path` juggling followed by a `grep|awk|tr` loop over four files — the same
    # value, resolved three ways, in one function.
    runner_config_value server_url "" appium_url
}

parse_host_port() {
    local URL="$1"
    local RAW="${URL#*://}"
    RAW="${RAW%%/*}"
    local HOST=""
    local PORT=""
    if [[ "${RAW}" == *:* ]]; then
        HOST="${RAW%:*}"
        PORT="${RAW##*:}"
    else
        HOST="${RAW}"
        PORT="4723"
    fi
    echo "${HOST} ${PORT}"
}

get_health_check_url() {
    local HOST="$1"
    local PORT="$2"
    if [[ "${HOST}" == "0.0.0.0" ]]; then
        HOST="127.0.0.1"
    fi
    echo "http://${HOST}:${PORT}/status"
}

DEFAULT_HOST="127.0.0.1"
DEFAULT_PORT="4723"

CONFIG_URL="${APPIUM_SERVER_URL:-${APPIUM_URL:-}}"
if [[ -z "${CONFIG_URL}" ]]; then
    CONFIG_URL="$(resolve_config_url)"
fi

if [[ -n "${CONFIG_URL}" ]]; then
    read -r PARSED_HOST PARSED_PORT <<< "$(parse_host_port "${CONFIG_URL}")"
    DEFAULT_HOST="${PARSED_HOST:-${DEFAULT_HOST}}"
    DEFAULT_PORT="${PARSED_PORT:-${DEFAULT_PORT}}"
fi

APPIUM_HOST="${APPIUM_HOST:-${DEFAULT_HOST}}"
APPIUM_PORT="${APPIUM_PORT:-${DEFAULT_PORT}}"

APPIUM_BIN="$(runner_find_binary appium "$HOME/.volta/bin/appium" /opt/homebrew/bin/appium /usr/local/bin/appium)"

# The one description of the Appium automation server (ticket #476). `--port` and
# `--address` can move it at launch time, so `cmd_start` re-describes it after parsing.
configure_service() {
    runner_service_reset
    RUNNER_SERVICE_LABEL="Appium"
    RUNNER_SERVICE_NAME="Appium server"
    RUNNER_SERVICE_PID_FILE="${PID_FILE}"
    RUNNER_SERVICE_LOG_FILE="${LOG_FILE}"
    RUNNER_SERVICE_PORT="${APPIUM_PORT}"
    RUNNER_SERVICE_HEALTH_URL="$(get_health_check_url "${APPIUM_HOST}" "${APPIUM_PORT}")"
    RUNNER_SERVICE_ENDPOINT="http://${APPIUM_HOST}:${APPIUM_PORT}"
    RUNNER_SERVICE_STOP_TIMEOUT_SEC="${APPIUM_STOP_TIMEOUT_SEC}"
    RUNNER_SERVICE_STOP_DETAIL="Appium automation server"
    RUNNER_SERVICE_STOP_COMPLETE_DETAIL="Appium automation server stopped"
    RUNNER_SERVICE_RESTART_DETAIL="Appium automation server"
    RUNNER_SERVICE_RESTART_COMPLETE_DETAIL="Appium automation server back online"
    RUNNER_SERVICE_RESIDUAL_PATTERN="appium.*${APPIUM_PORT}"
    # `cmd_start` exits from inside its own health check when the server is already up.
    RUNNER_SERVICE_RESTART_ISOLATED="1"
    RUNNER_SERVICE_STOP_BANNER="🛑 Stopping local Appium server..."
    RUNNER_SERVICE_IDLE_MESSAGE="No running Appium process found."
    RUNNER_SERVICE_STOPPED_MESSAGE="Appium server stopped successfully."
    RUNNER_SERVICE_STATUS_CHECKING="Checking Appium server status..."
    RUNNER_SERVICE_STATUS_UP="🟢 Appium server is RUNNING and HEALTHY at http://${APPIUM_HOST}:${APPIUM_PORT}"
    RUNNER_SERVICE_STATUS_DOWN="🔴 Appium server is NOT RUNNING at http://${APPIUM_HOST}:${APPIUM_PORT}"
    RUNNER_SERVICE_STATUS_PID_LINE="   Process PID : {pid}"
    RUNNER_SERVICE_STATUS_LOG_LINE="   Log file    : {log}"
}

configure_service

cmd_status() {
    runner_service_status
}

cmd_stop() {
    runner_service_stop
}

cmd_start() {
    local DAEMON=0
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -d|--daemon)
                DAEMON=1
                shift
                ;;
            --port|-p)
                APPIUM_PORT="$2"
                shift 2
                ;;
            --address|-a|--host|-h)
                APPIUM_HOST="$2"
                shift 2
                ;;
            *)
                shift
                ;;
        esac
    done

    # `--port` / `--address` may have moved the service; the description follows.
    configure_service
    local HEALTH_URL
    HEALTH_URL="$(get_health_check_url "${APPIUM_HOST}" "${APPIUM_PORT}")"
    local STATUS_ENDPOINT="http://${APPIUM_HOST}:${APPIUM_PORT}/status"

    if [[ -z "${APPIUM_BIN}" ]]; then
        echo "❌ Error: 'appium' binary not found in PATH." >&2
        echo "💡 Install Appium via: npm install -g appium" >&2
        exit 1
    fi

    # Check if already running
    if runner_service_healthy ""; then
        local RUNNING_PID
        RUNNING_PID="$(runner_service_resolve_pid)"
        if [[ ${DAEMON} -eq 1 ]]; then
            echo "ℹ️ Appium is already running in background (PID: ${RUNNING_PID:-unknown}) at http://${APPIUM_HOST}:${APPIUM_PORT}"
            exit 0
        else
            runner_attached_logs "${RUNNING_PID:-unknown}" "${LOG_FILE}" "Appium server" "http://${APPIUM_HOST}:${APPIUM_PORT}" "${PID_FILE}"
            exit 0
        fi
    fi

    if [[ ${DAEMON} -eq 1 ]]; then
        echo "🚀 Starting Appium in background on http://${APPIUM_HOST}:${APPIUM_PORT}..."
        "${APPIUM_BIN}" --address "${APPIUM_HOST}" --port "${APPIUM_PORT}" --relaxed-security >> "${LOG_FILE}" 2>&1 &
        local PID=$!
        echo "${PID}" > "${PID_FILE}"

        # Wait for health check
        for _ in {1..30}; do
            if curl -s -f "${HEALTH_URL}" >/dev/null 2>&1; then
                echo "✅ Appium successfully started in background (PID: ${PID})"
                echo "   Status Endpoint : ${STATUS_ENDPOINT}"
                echo "   Log File        : ${LOG_FILE}"
                exit 0
            fi
            sleep 0.2
        done
        echo "⚠️ Appium failed to respond to status check within 6s. Check ${LOG_FILE}" >&2
        exit 1
    else
        echo "🚀 Starting Appium on http://${APPIUM_HOST}:${APPIUM_PORT}..."
        echo "   Status Endpoint : ${STATUS_ENDPOINT}"
        echo "   Log File        : ${LOG_FILE}"
        echo "   Press Ctrl+C to stop."
        echo ""

        "${APPIUM_BIN}" --address "${APPIUM_HOST}" --port "${APPIUM_PORT}" --relaxed-security >> "${LOG_FILE}" 2>&1 &
        local PID=$!
        echo "${PID}" > "${PID_FILE}"

        trap 'runner_watch_detach; echo -e "\n🛑 Stopping Appium (PID: '"${PID}"')..."; kill '"${PID}"' 2>/dev/null || true; rm -f '"${PID_FILE}"'; exit 0' INT TERM

        # The same liveness watch the attach path uses (ticket #425): a bare `tail -n 0 -f`
        # left the terminal on a live cursor over an inert file after this server was killed
        # from another terminal. Ctrl+C legitimately *does* stop the server here — this
        # process launched it and owns it — so the handler above still signals it; the watch
        # only decides when the stream ends on its own.
        runner_watch_log_stream "${PID}" "${LOG_FILE}" "Appium server" 0 "${PID_FILE}"
    fi
}

cmd_restart() {
    echo "🔄 Restarting Appium server..."
    runner_service_restart "$@"
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
