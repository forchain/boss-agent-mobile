#!/usr/bin/env bash
# ==============================================================================
# Boss Agent Mobile - SvelteKit Web Dashboard Runner
# ==============================================================================
# Starts the full-stack SvelteKit Web Dashboard on http://127.0.0.1:5173 with
# persistent logging and auto-attach if already running.
#
# Usage:
#   ./web.sh
#   ./web.sh start
#   ./web.sh start --daemon
#   ./web.sh stop
#   ./web.sh restart
#   ./web.sh restart --daemon
#   ./web.sh status
#   POCKETBASE_URL=http://192.168.1.100:8090 ./web.sh
# ==============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

# Shared process-lifecycle primitives. This script's semantics were the model for the
# library — its LISTEN-only port probe is the rule the other runners now inherit.
# shellcheck source=runner_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/runner_lib.sh"

mkdir -p ".boss_agent"

PID_FILE=".boss_agent/web.pid"
LOG_FILE=".boss_agent/web.log"
WEB_HOST="${WEB_HOST:-0.0.0.0}"
WEB_PORT="${WEB_PORT:-5173}"
WEB_URL="http://${WEB_HOST}:${WEB_PORT}"

# Seconds to wait for a graceful exit before escalating to SIGKILL, and again for the
# listening socket to be handed back to the OS.
WEB_STOP_TIMEOUT_SEC="${WEB_STOP_TIMEOUT_SEC:-10}"

process_alive() {
    runner_process_alive "${1:-}"
}

get_running_web_pid() {
    if [[ -f "${PID_FILE}" ]]; then
        local PID
        PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
        if [[ -n "${PID}" ]] && process_alive "${PID}"; then
            echo "${PID}"
            return 0
        fi
    fi

    # Fallback to the process listening on the port
    local PORT_PID
    PORT_PID="$(listening_pid)"
    if [[ -n "${PORT_PID}" ]]; then
        echo "${PORT_PID}" > "${PID_FILE}"
        echo "${PORT_PID}"
        return 0
    fi
    echo ""
}

listening_pid() {
    # LISTEN-only: a browser, curl, or a test client merely *connected* to the port must
    # never be mistaken for the dashboard and must never be signalled.
    runner_port_listener_pid "${WEB_PORT}"
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

    if curl -s -f "http://127.0.0.1:${WEB_PORT}" >/dev/null 2>&1 || curl -s -f "${WEB_URL}" >/dev/null 2>&1; then
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

log_web_event() {
    runner_log_event "${LOG_FILE}" "$1"
}

port_in_use() {
    [[ -n "$(listening_pid)" ]]
}

# Wait until the given predicate (a command receiving `PREDICATE_ARGS`) reports success,
# re-checking once at the deadline so a state change during the final interval still counts.
wait_until() {
    runner_wait_until "$@"
}

process_gone() {
    runner_process_gone "${1:-}"
}

port_released() {
    ! port_in_use
}

cmd_stop() {
    echo "🛑 Stopping SvelteKit Web Dashboard..."
    local STOPPED=0
    local PID
    PID="$(get_running_web_pid)"

    if [[ -n "${PID}" ]]; then
        log_web_event "🛑 [Web] ${RUNNER_WEB_SHUTDOWN_ACK}, shutting down Web Dashboard... (PID: ${PID}, port: ${WEB_PORT})"
        echo "   Shutdown feedback appended to ${LOG_FILE}"
        pkill -P "${PID}" 2>/dev/null || true

        # SIGTERM first, the full budget to exit cooperatively (in-flight tasks release
        # their leases), then SIGKILL. The escalation itself is the library's, so every
        # service escalates identically.
        if ! runner_graceful_stop "${PID}" "${WEB_STOP_TIMEOUT_SEC}" "Web Dashboard"; then
            log_web_event "⚠️ [Web] Graceful shutdown timed out after ${WEB_STOP_TIMEOUT_SEC}s; sending SIGKILL to PID ${PID}."
        fi
        STOPPED=1
    fi

    # Reclaim the port from any process that outlived its parent (e.g. a detached vite dev server)
    pkill -f "vite dev.*${WEB_PORT}" 2>/dev/null || true
    if port_in_use; then
        local PORT_PID
        PORT_PID="$(listening_pid)"
        if [[ -n "${PORT_PID}" ]]; then
            echo "⚠️ Port ${WEB_PORT} still held by PID ${PORT_PID}; reclaiming."
            runner_graceful_stop "${PORT_PID}" "${WEB_STOP_TIMEOUT_SEC}" "port ${WEB_PORT} listener"
        fi
    fi

    rm -f "${PID_FILE}"

    if ! wait_until "${WEB_STOP_TIMEOUT_SEC}" port_released; then
        echo "❌ Error: port ${WEB_PORT} is still occupied after shutdown." >&2
        log_web_event "❌ [Web] Port ${WEB_PORT} is still occupied after shutdown."
        return 1
    fi

    if [[ ${STOPPED} -eq 1 ]]; then
        log_web_event "✅ [Web] Web Dashboard stopped; port ${WEB_PORT} released."
        echo "✅ SvelteKit Web Dashboard stopped (port ${WEB_PORT} released)."
    else
        echo "ℹ️ No running Web Dashboard process found."
    fi
}

cmd_start() {
    local IS_DAEMON=0
    local VITE_ARGS=()
    for arg in "$@"; do
        if [[ "${arg}" == "--daemon" ]]; then
            IS_DAEMON=1
        else
            VITE_ARGS+=("${arg}")
        fi
    done

    # Check if already running locally
    local RUNNING_PID
    RUNNING_PID="$(get_running_web_pid)"
    if curl -s -f "http://127.0.0.1:${WEB_PORT}" >/dev/null 2>&1 || curl -s -f "${WEB_URL}" >/dev/null 2>&1; then
        if [[ "${IS_DAEMON}" -eq 1 || "${DAEMON:-0}" -eq 1 ]]; then
            echo "ℹ️ SvelteKit Web Dashboard is already running in background (PID: ${RUNNING_PID:-unknown}) at ${WEB_URL}"
            return 0
        else
            attach_logs "${RUNNING_PID:-unknown}"
            return 0
        fi
    fi

    # Check Node / npm environment
    if ! command -v npm >/dev/null 2>&1; then
        echo "❌ Error: 'npm' is not installed or not in PATH." >&2
        exit 1
    fi

    # Ensure dependencies are installed
    if [[ ! -d "web/node_modules" ]]; then
        echo "📦 Installing web frontend dependencies (web/node_modules missing)..."
        npm --prefix web install
    fi

    # Ensure SvelteKit types & tsconfig are generated
    if [[ ! -f "web/.svelte-kit/tsconfig.json" ]]; then
        (cd web && npx svelte-kit sync)
    fi

    # Check dependency: PocketBase health
    # One config read, through the library: the CLI resolves the precedence chain and
    # the environment; its single grep fallback covers a copied script root.
    export POCKETBASE_URL="${POCKETBASE_URL:-$(runner_config_value pocketbase_url http://127.0.0.1:8090 pb_url)}"
    export VITE_POCKETBASE_URL="${POCKETBASE_URL}"
    export PUBLIC_POCKETBASE_URL="${POCKETBASE_URL}"
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
    echo "   PocketBase URL : ${POCKETBASE_URL}"
    echo "   Log File       : ${LOG_FILE}"
    echo "   Press Ctrl+C to stop."
    echo ""

    if [[ "${IS_DAEMON}" -eq 1 || "${DAEMON:-0}" -eq 1 ]]; then
        if [[ ${#VITE_ARGS[@]} -gt 0 ]]; then
            nohup env HOST="${WEB_HOST}" PORT="${WEB_PORT}" VITE_POCKETBASE_URL="${POCKETBASE_URL}" PUBLIC_POCKETBASE_URL="${POCKETBASE_URL}" npm --prefix web run dev -- --host "${WEB_HOST}" --port "${WEB_PORT}" "${VITE_ARGS[@]}" >> "${LOG_FILE}" 2>&1 &
        else
            nohup env HOST="${WEB_HOST}" PORT="${WEB_PORT}" VITE_POCKETBASE_URL="${POCKETBASE_URL}" PUBLIC_POCKETBASE_URL="${POCKETBASE_URL}" npm --prefix web run dev -- --host "${WEB_HOST}" --port "${WEB_PORT}" >> "${LOG_FILE}" 2>&1 &
        fi
        local PID=$!
        echo "${PID}" > "${PID_FILE}"
        echo "✅ SvelteKit Web Dashboard started in background (PID: ${PID})."
        echo "   Logs: ${LOG_FILE}"
        return 0
    fi

    # Start in background, capture PID, pipe to log and tail
    if [[ ${#VITE_ARGS[@]} -gt 0 ]]; then
        HOST="${WEB_HOST}" PORT="${WEB_PORT}" VITE_POCKETBASE_URL="${POCKETBASE_URL}" PUBLIC_POCKETBASE_URL="${POCKETBASE_URL}" npm --prefix web run dev -- --host "${WEB_HOST}" --port "${WEB_PORT}" "${VITE_ARGS[@]}" >> "${LOG_FILE}" 2>&1 &
    else
        HOST="${WEB_HOST}" PORT="${WEB_PORT}" VITE_POCKETBASE_URL="${POCKETBASE_URL}" PUBLIC_POCKETBASE_URL="${POCKETBASE_URL}" npm --prefix web run dev -- --host "${WEB_HOST}" --port "${WEB_PORT}" >> "${LOG_FILE}" 2>&1 &
    fi
    local PID=$!
    echo "${PID}" > "${PID_FILE}"

    trap 'echo -e "\n🛑 Stopping Web Dashboard (PID: '"${PID}"')..."; kill '"${PID}"' 2>/dev/null || true; rm -f '"${PID_FILE}"'; exit 0' INT TERM

    tail -n 0 -f "${LOG_FILE}"
}

cmd_restart() {
    echo "🔄 Restarting SvelteKit Web Dashboard..."
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
