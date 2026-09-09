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

mkdir -p ".boss_agent"

PID_FILE=".boss_agent/appium.pid"
LOG_FILE=".boss_agent/appium.log"

resolve_config_url() {
    # 1. Try python3 if available to query centralized loader
    if command -v python3 >/dev/null 2>&1; then
        local PY_URL
        PY_URL="$(python3 -c "import sys; sys.path.insert(0, 'src'); from boss_agent.settings import resolve_server_url; print(resolve_server_url())" 2>/dev/null || true)"
        if [[ -n "${PY_URL}" ]]; then
            echo "${PY_URL}"
            return 0
        fi
    fi

    # 2. Fallback: simple grep across config files in precedence order
    for conf in "config/settings.local.yaml" "config/settings.local.json" "config/settings.yaml" "config/settings.example.yaml"; do
        if [[ -f "${conf}" ]]; then
            local LINE VAL
            LINE="$(grep -E "^[[:space:]]*(server_url|appium_url):" "${conf}" 2>/dev/null | head -n 1 || true)"
            if [[ -n "${LINE}" ]]; then
                VAL="$(echo "${LINE}" | awk '{print $2}' | tr -d '"' | tr -d "'" || true)"
                if [[ -n "${VAL}" ]]; then
                    echo "${VAL}"
                    return 0
                fi
            fi
        fi
    done
    echo ""
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

find_appium_binary() {
    if command -v appium >/dev/null 2>&1; then
        echo "appium"
    elif [[ -x "$HOME/.volta/bin/appium" ]]; then
        echo "$HOME/.volta/bin/appium"
    elif [[ -x "/opt/homebrew/bin/appium" ]]; then
        echo "/opt/homebrew/bin/appium"
    elif [[ -x "/usr/local/bin/appium" ]]; then
        echo "/usr/local/bin/appium"
    else
        echo ""
    fi
}

APPIUM_BIN="$(find_appium_binary)"

get_running_appium_pid() {
    if [[ -f "${PID_FILE}" ]]; then
        local PID
        PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
        if [[ -n "${PID}" ]] && ps -p "${PID}" >/dev/null 2>&1; then
            echo "${PID}"
            return 0
        fi
    fi

    # Fallback to lsof on Appium port
    local PORT_PID
    PORT_PID="$(lsof -ti ":${APPIUM_PORT}" 2>/dev/null | head -n 1 || true)"
    if [[ -n "${PORT_PID}" ]]; then
        echo "${PORT_PID}" > "${PID_FILE}"
        echo "${PORT_PID}"
        return 0
    fi
    echo ""
}

attach_logs() {
    local PID="$1"
    local ENDPOINT="http://${APPIUM_HOST}:${APPIUM_PORT}"

    echo "ℹ️ Appium server is already running (PID: ${PID}) at ${ENDPOINT}"
    echo "👀 Attaching to live log stream (${LOG_FILE})... (Press Ctrl+C to detach)"
    echo "----------------------------------------------------------------------"

    trap 'echo -e "\n👋 Detached from Appium logs (Appium server is still running in background)."; exit 0' INT TERM

    if [[ ! -f "${LOG_FILE}" ]]; then
        touch "${LOG_FILE}"
    fi

    exec tail -n 30 -f "${LOG_FILE}"
}

cmd_status() {
    echo "🔍 Checking Appium server status..."
    local PID
    PID="$(get_running_appium_pid)"
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
    PID="$(get_running_appium_pid)"

    if [[ -n "${PID}" ]]; then
        kill "${PID}" 2>/dev/null || true
        sleep 0.5
        kill -9 "${PID}" 2>/dev/null || true
        STOPPED=1
    fi
    rm -f "${PID_FILE}"

    # Cleanup any lingering process
    pkill -f "appium.*${APPIUM_PORT}" 2>/dev/null || true

    if [[ ${STOPPED} -eq 1 ]]; then
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
        RUNNING_PID="$(get_running_appium_pid)"
        if [[ ${DAEMON} -eq 1 ]]; then
            echo "ℹ️ Appium is already running in background (PID: ${RUNNING_PID:-unknown}) at http://${APPIUM_HOST}:${APPIUM_PORT}"
            exit 0
        else
            attach_logs "${RUNNING_PID:-unknown}"
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
    cmd_stop
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
