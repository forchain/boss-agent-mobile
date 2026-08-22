#!/usr/bin/env bash
# ==============================================================================
# Boss Agent Mobile - SvelteKit Web Dashboard Runner
# ==============================================================================
# Starts the full-stack SvelteKit Web Dashboard on http://127.0.0.1:5173 with
# persistent logging and auto-attach if already running.
#
# Usage:
#   ./web.sh
#   ./web.sh stop
#   ./web.sh status
#   POCKETBASE_URL=http://192.168.1.100:8090 ./web.sh
# ==============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

mkdir -p ".boss_agent"

PID_FILE=".boss_agent/web.pid"
LOG_FILE=".boss_agent/web.log"
WEB_URL="http://127.0.0.1:5173"

get_running_web_pid() {
    if [[ -f "${PID_FILE}" ]]; then
        local PID
        PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
        if [[ -n "${PID}" ]] && ps -p "${PID}" >/dev/null 2>&1; then
            echo "${PID}"
            return 0
        fi
    fi

    # Fallback to lsof on port 5173
    local PORT_PID
    PORT_PID="$(lsof -ti :5173 2>/dev/null | head -n 1 || true)"
    if [[ -n "${PORT_PID}" ]]; then
        echo "${PORT_PID}" > "${PID_FILE}"
        echo "${PORT_PID}"
        return 0
    fi
    echo ""
}

attach_logs() {
    local PID="$1"
    echo "ℹ️ SvelteKit Web Dashboard is already running (PID: ${PID}) at ${WEB_URL}"
    echo "👀 Attaching to live log stream (${LOG_FILE})... (Press Ctrl+C to detach)"
    echo "----------------------------------------------------------------------"

    trap 'echo -e "\n👋 Detached from Web Dashboard logs (Web server is still running in background)."; exit 0' INT TERM

    if [[ ! -f "${LOG_FILE}" ]]; then
        touch "${LOG_FILE}"
    fi

    exec tail -n 30 -f "${LOG_FILE}"
}

cmd_status() {
    echo "🔍 Checking SvelteKit Web Dashboard status..."
    local PID
    PID="$(get_running_web_pid)"

    if curl -s -f "${WEB_URL}" >/dev/null 2>&1; then
        echo "🟢 SvelteKit Web Dashboard is RUNNING at ${WEB_URL}"
        if [[ -n "${PID}" ]]; then
            echo "   Process PID : ${PID}"
        fi
        echo "   Log file    : ${LOG_FILE}"
        return 0
    else
        echo "🔴 SvelteKit Web Dashboard is NOT RUNNING at ${WEB_URL}"
        if [[ -n "${PID}" ]]; then
            rm -f "${PID_FILE}"
        fi
        return 1
    fi
}

cmd_stop() {
    echo "🛑 Stopping SvelteKit Web Dashboard..."
    local STOPPED=0
    local PID
    PID="$(get_running_web_pid)"

    if [[ -n "${PID}" ]]; then
        kill "${PID}" 2>/dev/null || true
        sleep 0.5
        kill -9 "${PID}" 2>/dev/null || true
        STOPPED=1
    fi
    rm -f "${PID_FILE}"

    # Cleanup vite process
    pkill -f "vite dev.*5173" 2>/dev/null || true

    if [[ ${STOPPED} -eq 1 ]]; then
        echo "✅ SvelteKit Web Dashboard stopped."
    else
        echo "ℹ️ No running Web Dashboard process found."
    fi
}

cmd_start() {
    # Check if already running locally
    local RUNNING_PID
    RUNNING_PID="$(get_running_web_pid)"
    if curl -s -f "${WEB_URL}" >/dev/null 2>&1; then
        attach_logs "${RUNNING_PID:-unknown}"
    fi

    # Check Node / npm environment
    if ! command -v npm >/dev/null 2>&1; then
        echo "❌ Error: 'npm' is not installed or not in PATH." >&2
        exit 1
    fi

    # Check dependency: PocketBase health
    POCKETBASE_URL="${POCKETBASE_URL:-http://127.0.0.1:8090}"
    HEALTH_URL="${POCKETBASE_URL%/}/api/health"

    echo "🔍 Checking PocketBase State Stream dependency at ${HEALTH_URL}..."
    if ! curl -s -f "${HEALTH_URL}" >/dev/null 2>&1; then
        echo "❌ Error: PocketBase is not reachable at ${HEALTH_URL}" >&2
        echo "" >&2
        echo "💡 PocketBase State Stream broker must be running first:" >&2
        echo "   - Local PocketBase: run './pb.sh' or './run.sh pb' in another terminal" >&2
        echo "   - Remote PocketBase: export POCKETBASE_URL=\"http://<remote-ip>:<port>\"" >&2
        echo "" >&2
        exit 1
    fi

    echo "✅ PocketBase State Stream dependency is healthy (${POCKETBASE_URL})"
    echo "🌐 Starting Boss Agent Mobile SvelteKit Web Dashboard on ${WEB_URL}..."
    echo "   Log File : ${LOG_FILE}"
    echo "   Press Ctrl+C to stop."
    echo ""

    # Start in background, capture PID, pipe to log and tail
    npm --prefix web run dev "$@" >> "${LOG_FILE}" 2>&1 &
    local PID=$!
    echo "${PID}" > "${PID_FILE}"

    trap 'echo -e "\n🛑 Stopping Web Dashboard (PID: '"${PID}"')..."; kill '"${PID}"' 2>/dev/null || true; rm -f '"${PID_FILE}"'; exit 0' INT TERM

    tail -n 0 -f "${LOG_FILE}"
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
    *)
        cmd_start "$@"
        ;;
esac
