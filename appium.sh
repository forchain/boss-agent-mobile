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

cmd_status() {
    echo "🔍 Checking Appium server status..."
    local PID
    PID="$(runner_resolve_pid "${PID_FILE}" "${APPIUM_PORT}")"
    local HEALTH_URL
    HEALTH_URL="$(get_health_check_url "${APPIUM_HOST}" "${APPIUM_PORT}")"

    if curl -s -f "${HEALTH_URL}" >/dev/null 2>&1; then
        echo "🟢 Appium server is RUNNING and HEALTHY at http://${APPIUM_HOST}:${APPIUM_PORT}"
        if [[ -n "${PID}" ]]; then
            echo "   Process PID : ${PID}"
        fi
        echo "   Log file    : ${LOG_FILE}"
        return 0
    else
        echo "🔴 Appium server is NOT RUNNING at http://${APPIUM_HOST}:${APPIUM_PORT}"
        if [[ -n "${PID}" ]]; then
            rm -f "${PID_FILE}"
        fi
        return 1
    fi
}

cmd_stop() {
    echo "🛑 Stopping local Appium server..."
    local STOPPED=0
    local PID
    PID="$(runner_resolve_pid "${PID_FILE}" "${APPIUM_PORT}")"

    if [[ -n "${PID}" ]]; then
        runner_log_stop_request "${LOG_FILE}" "Appium" "${PID}" "Appium automation server"
        runner_graceful_stop "${PID}" "${APPIUM_STOP_TIMEOUT_SEC}" "Appium" "${LOG_FILE}"
        STOPPED=1
    fi
    runner_pidfile_clear "${PID_FILE}"

    # Anything still listening outlived its parent. LISTEN-only, so a connected client
    # is never a candidate.
    local PORT_PID
    PORT_PID="$(runner_port_listener_pid "${APPIUM_PORT}")"
    if [[ -n "${PORT_PID}" ]]; then
        echo "⚠️ Port ${APPIUM_PORT} still held by PID ${PORT_PID}; reclaiming."
        runner_graceful_stop "${PORT_PID}" "${APPIUM_STOP_TIMEOUT_SEC}" "Appium listener" "${LOG_FILE}"
        STOPPED=1
    fi

    # Cleanup any lingering process
    local LINGER_PIDS
    LINGER_PIDS="$(pgrep -f "appium.*${APPIUM_PORT}" 2>/dev/null || true)"
    if [[ -n "${LINGER_PIDS}" ]]; then
        local LINGER_PID
        for LINGER_PID in ${LINGER_PIDS}; do
            runner_graceful_stop "${LINGER_PID}" "${APPIUM_STOP_TIMEOUT_SEC}" "Appium" "${LOG_FILE}"
        done
    fi

    if [[ ${STOPPED} -eq 1 ]]; then
        runner_log_stop_complete "${LOG_FILE}" "Appium" "Appium automation server stopped"
        echo "✅ Appium server stopped successfully."
    else
        echo "ℹ️ No running Appium process found."
    fi
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

    local HEALTH_URL
    HEALTH_URL="$(get_health_check_url "${APPIUM_HOST}" "${APPIUM_PORT}")"
    local STATUS_ENDPOINT="http://${APPIUM_HOST}:${APPIUM_PORT}/status"

    if [[ -z "${APPIUM_BIN}" ]]; then
        echo "❌ Error: 'appium' binary not found in PATH." >&2
        echo "💡 Install Appium via: npm install -g appium" >&2
        exit 1
    fi

    # Check if already running
    if curl -s -f "${HEALTH_URL}" >/dev/null 2>&1; then
        local RUNNING_PID
        RUNNING_PID="$(runner_resolve_pid "${PID_FILE}" "${APPIUM_PORT}")"
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

        trap 'echo -e "\n🛑 Stopping Appium (PID: '"${PID}"')..."; kill '"${PID}"' 2>/dev/null || true; rm -f '"${PID_FILE}"'; exit 0' INT TERM

        tail -n 0 -f "${LOG_FILE}"
    fi
}

cmd_restart() {
    echo "🔄 Restarting Appium server..."
    runner_log_restart_request "${LOG_FILE}" "Appium" "Appium automation server"
    cmd_stop
    sleep 0.5
    # In a subshell: `cmd_start` exits from inside its own health check when the server is
    # already up, and an `exit` there would otherwise take this confirmation down with it.
    #
    # The status is captured rather than propagated, so a start that failed cannot be
    # recorded below as a server that came back up. Not written as `( cmd_start ) || return 1`:
    # a subshell used as an operand of `||` has errexit suspended *inside* it, which would
    # let a failed pre-flight fall through to the foreground `tail -f` and hang forever.
    # Suspension is therefore lifted only for the outer shell, and restored for the subshell
    # where `cmd_start` depends on it to abort.
    set +e
    ( set -e; cmd_start "$@" )
    local START_STATUS=$?
    set -e
    if [[ ${START_STATUS} -ne 0 ]]; then
        return 1
    fi
    runner_log_restart_complete "${LOG_FILE}" "Appium" "Appium automation server back online"
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
