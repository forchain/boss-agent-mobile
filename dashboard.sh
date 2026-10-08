#!/usr/bin/env bash
# ==============================================================================
# Boss Agent Mobile - SvelteKit Web Dashboard Runner
# ==============================================================================
# Starts the full-stack SvelteKit Web Dashboard on http://127.0.0.1:5173 with
# persistent logging and auto-attach if already running.
#
# Usage:
#   ./dashboard.sh
#   ./dashboard.sh start
#   ./dashboard.sh start --daemon
#   ./dashboard.sh stop
#   ./dashboard.sh restart
#   ./dashboard.sh restart --daemon
#   ./dashboard.sh status
#   POCKETBASE_URL=http://192.168.1.100:8090 ./dashboard.sh
#   ./run.sh web <cmd>                 # Short orchestrator route for ./dashboard.sh
#
# The runner is named `dashboard.sh` rather than `web.sh` because the project root also
# holds the `web/` frontend source directory: under one name, `./web<Tab>` stalled on two
# candidates. The runtime files stay `.boss_agent/web.pid` / `.boss_agent/web.log`, which
# is the on-disk contract the teardown gate and the `WEB_PORT` env var already speak.
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

get_running_web_pid() {
    if [[ -f "${PID_FILE}" ]]; then
        local PID
        PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
        if [[ -n "${PID}" ]] && runner_process_alive "${PID}"; then
            if ! runner_process_cwd_alive "${PID}"; then
                rm -f "${PID_FILE}"
            else
                echo "${PID}"
                return 0
            fi
        fi
    fi

    # Fallback to the process listening on the port
    local PORT_PID
    PORT_PID="$(runner_port_listener_pid "${WEB_PORT}")"
    if [[ -n "${PORT_PID}" ]]; then
        if runner_process_cwd_alive "${PORT_PID}"; then
            echo "${PORT_PID}" > "${PID_FILE}"
            echo "${PORT_PID}"
            return 0
        fi
    fi
    echo ""
}

cmd_status() {
    echo "🔍 Checking SvelteKit Web Dashboard status..."
    local PID
    PID="$(get_running_web_pid)"
    local PORT_PID
    PORT_PID="$(runner_port_listener_pid "${WEB_PORT}")"

    if [[ -n "${PORT_PID}" ]] && ! runner_process_cwd_alive "${PORT_PID}"; then
        local STALE_CWD
        STALE_CWD="$(runner_process_cwd "${PORT_PID}")"
        echo "🔴 SvelteKit Web Dashboard port ${WEB_PORT} is held by a STALE process (PID: ${PORT_PID})"
        echo "   Deleted CWD: ${STALE_CWD:-unknown}"
        echo "   Run './dashboard.sh restart' to reclaim port and start a fresh server."
        return 1
    fi

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

cmd_stop() {
    echo "🛑 Stopping SvelteKit Web Dashboard..."
    local STOPPED=0
    local PID
    PID="$(get_running_web_pid)"

    if [[ -n "${PID}" ]]; then
        runner_log_event "${LOG_FILE}" "🛑 [Web] ${RUNNER_WEB_SHUTDOWN_ACK}, shutting down Web Dashboard... (PID: ${PID}, port: ${WEB_PORT})"
        echo "   Shutdown feedback appended to ${LOG_FILE}"
        pkill -P "${PID}" 2>/dev/null || true

        # SIGTERM first, the full budget to exit cooperatively (in-flight tasks release
        # their leases), then SIGKILL. The escalation itself is the library's, so every
        # service escalates identically.
        if ! runner_graceful_stop "${PID}" "${WEB_STOP_TIMEOUT_SEC}" "Web Dashboard"; then
            runner_log_event "${LOG_FILE}" "⚠️ [Web] Graceful shutdown timed out after ${WEB_STOP_TIMEOUT_SEC}s; sending SIGKILL to PID ${PID}."
        fi
        STOPPED=1
    fi

    # Reclaim the port from any process that outlived its parent (e.g. a detached vite dev server)
    pkill -f "vite dev.*${WEB_PORT}" 2>/dev/null || true
    if runner_port_in_use "${WEB_PORT}"; then
        local PORT_PID
        PORT_PID="$(runner_port_listener_pid "${WEB_PORT}")"
        if [[ -n "${PORT_PID}" ]]; then
            echo "⚠️ Port ${WEB_PORT} still held by PID ${PORT_PID}; reclaiming."
            runner_graceful_stop "${PORT_PID}" "${WEB_STOP_TIMEOUT_SEC}" "port ${WEB_PORT} listener"
        fi
    fi

    rm -f "${PID_FILE}"

    if ! runner_wait_until "${WEB_STOP_TIMEOUT_SEC}" runner_port_released "${WEB_PORT}"; then
        echo "❌ Error: port ${WEB_PORT} is still occupied after shutdown." >&2
        runner_log_event "${LOG_FILE}" "❌ [Web] Port ${WEB_PORT} is still occupied after shutdown."
        return 1
    fi

    if [[ ${STOPPED} -eq 1 ]]; then
        runner_log_event "${LOG_FILE}" "✅ [Web] Web Dashboard stopped; port ${WEB_PORT} released."
        echo "✅ SvelteKit Web Dashboard stopped (port ${WEB_PORT} released)."
    else
        echo "ℹ️ No running Web Dashboard process found."
    fi
}

resolve_version_env() {
    # Navbar version badge (#421, #422).
    #
    # Rule: whichever variable the operator set explicitly must win, and the sniff must
    # only ever *populate* a variable the operator left alone -- never manufacture one
    # that outranks an explicit choice. That matters because
    # `web/src/lib/server/version.ts` reads APP_VERSION *before* PUBLIC_APP_VERSION: if
    # we sniffed APP_VERSION unconditionally, an operator who exported only
    # PUBLIC_APP_VERSION would see the git tag win and their value silently ignored.
    # Ticket #421 promises either variable can drive the override, so we mirror the
    # operator's value into whichever one they left unset.
    #
    # With neither set we sniff the live checkout's git tag and publish it under both
    # names, so both halves of the resolver agree. Every `git describe` failure -- no
    # git binary, not a repo, shallow clone, empty output -- degrades to empty through
    # `|| true` (never tripping `set -euo pipefail`, line 26), leaving the server
    # resolver to fall through to version.json and then the v0.1 default.
    #
    # Exported from a function rather than inlined in `cmd_start` so the E2E suite can
    # exercise this exact block (`tests/e2e/test_dashboard_version_env.py`) without
    # starting the dashboard. `export` inside a function still exports to the calling
    # shell, so the launch sites below are unchanged.
    local sniffed
    if [[ -z "${APP_VERSION:-}" && -z "${PUBLIC_APP_VERSION:-}" ]]; then
        sniffed="$(git describe --tags --always 2>/dev/null || true)"
        export APP_VERSION="${sniffed}"
        export PUBLIC_APP_VERSION="${sniffed}"
    elif [[ -z "${APP_VERSION:-}" ]]; then
        # Operator set only PUBLIC_APP_VERSION: promote it so the resolver's
        # APP_VERSION-first order cannot bury it under a sniffed tag.
        export APP_VERSION="${PUBLIC_APP_VERSION}"
    else
        # APP_VERSION set (possibly with PUBLIC_APP_VERSION too): leave PUBLIC alone
        # if the operator set it, otherwise mirror APP_VERSION across.
        export PUBLIC_APP_VERSION="${PUBLIC_APP_VERSION:-${APP_VERSION}}"
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

    # Reclaim the port if held by a stale process whose working directory was deleted
    local PORT_PID
    PORT_PID="$(runner_port_listener_pid "${WEB_PORT}")"
    if [[ -n "${PORT_PID}" ]] && ! runner_process_cwd_alive "${PORT_PID}"; then
        echo "⚠️ Port ${WEB_PORT} is held by stale process PID ${PORT_PID} whose working directory was deleted; reclaiming."
        runner_graceful_stop "${PORT_PID}" "${WEB_STOP_TIMEOUT_SEC}" "stale web listener"
        rm -f "${PID_FILE}"
    fi

    # Check if already running locally
    local RUNNING_PID
    RUNNING_PID="$(get_running_web_pid)"
    if curl -s -f "http://127.0.0.1:${WEB_PORT}" >/dev/null 2>&1 || curl -s -f "${WEB_URL}" >/dev/null 2>&1; then
        if [[ "${IS_DAEMON}" -eq 1 || "${DAEMON:-0}" -eq 1 ]]; then
            echo "ℹ️ SvelteKit Web Dashboard is already running in background (PID: ${RUNNING_PID:-unknown}) at ${WEB_URL}"
            return 0
        else
            runner_attached_logs "${RUNNING_PID:-unknown}" "${LOG_FILE}" "SvelteKit Web Dashboard" "${WEB_URL}"
            return 0
        fi
    fi

    # Check Node / npm environment
    if ! command -v npm >/dev/null 2>&1; then
        echo "❌ Error: 'npm' is not installed or not in PATH." >&2
        exit 1
    fi

    # Ensure dependencies are installed and in sync with package.json
    if [[ ! -d "web/node_modules" ]] || ! npm --prefix web ls --depth=0 >/dev/null 2>&1; then
        echo "📦 Installing web frontend dependencies (missing or unmet dependencies)..."
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

    # Navbar version badge (#421, #422). The precedence rules and the git-sniff degradation
    # are documented on `resolve_version_env`; the four launch sites below pass explicit
    # `env VAR=…` allowlists, so the export alone would NOT reach the npm process in the
    # daemon branch.
    resolve_version_env
    HEALTH_URL="${POCKETBASE_URL%/}/api/health"


    echo "🔍 Checking PocketBase State Stream dependency at ${HEALTH_URL}..."
    if ! curl -s -f "${HEALTH_URL}" >/dev/null 2>&1; then
        echo "❌ Error: PocketBase is not reachable at ${HEALTH_URL}" >&2
        echo "" >&2
        echo "💡 PocketBase State Stream broker must be running first:" >&2
        echo "   - Local PocketBase: run './pocketbase.sh' or './run.sh pb' in another terminal" >&2
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
            nohup env HOST="${WEB_HOST}" PORT="${WEB_PORT}" VITE_POCKETBASE_URL="${POCKETBASE_URL}" PUBLIC_POCKETBASE_URL="${POCKETBASE_URL}" APP_VERSION="${APP_VERSION}" PUBLIC_APP_VERSION="${PUBLIC_APP_VERSION}" npm --prefix web run dev -- --host "${WEB_HOST}" --port "${WEB_PORT}" "${VITE_ARGS[@]}" >> "${LOG_FILE}" 2>&1 &
        else
            nohup env HOST="${WEB_HOST}" PORT="${WEB_PORT}" VITE_POCKETBASE_URL="${POCKETBASE_URL}" PUBLIC_POCKETBASE_URL="${POCKETBASE_URL}" APP_VERSION="${APP_VERSION}" PUBLIC_APP_VERSION="${PUBLIC_APP_VERSION}" npm --prefix web run dev -- --host "${WEB_HOST}" --port "${WEB_PORT}" >> "${LOG_FILE}" 2>&1 &
        fi
        local PID=$!
        echo "${PID}" > "${PID_FILE}"
        echo "✅ SvelteKit Web Dashboard started in background (PID: ${PID})."
        echo "   Logs: ${LOG_FILE}"
        return 0
    fi

    # Start in background, capture PID, pipe to log and tail
    if [[ ${#VITE_ARGS[@]} -gt 0 ]]; then
        HOST="${WEB_HOST}" PORT="${WEB_PORT}" VITE_POCKETBASE_URL="${POCKETBASE_URL}" PUBLIC_POCKETBASE_URL="${POCKETBASE_URL}" APP_VERSION="${APP_VERSION}" PUBLIC_APP_VERSION="${PUBLIC_APP_VERSION}" npm --prefix web run dev -- --host "${WEB_HOST}" --port "${WEB_PORT}" "${VITE_ARGS[@]}" >> "${LOG_FILE}" 2>&1 &
    else
        HOST="${WEB_HOST}" PORT="${WEB_PORT}" VITE_POCKETBASE_URL="${POCKETBASE_URL}" PUBLIC_POCKETBASE_URL="${POCKETBASE_URL}" APP_VERSION="${APP_VERSION}" PUBLIC_APP_VERSION="${PUBLIC_APP_VERSION}" npm --prefix web run dev -- --host "${WEB_HOST}" --port "${WEB_PORT}" >> "${LOG_FILE}" 2>&1 &
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
