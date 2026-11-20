#!/usr/bin/env bash
# ==============================================================================
# Boss Agent Mobile - PocketBase Standalone Runner
# ==============================================================================
# Manages local PocketBase State Stream instance with persistent logging and
# auto-attach to live log stream if already running.
#
# Usage:
#   ./pocketbase.sh                   # Start or attach to PocketBase in foreground
#   ./pocketbase.sh start             # Start or attach to PocketBase in foreground
#   ./pocketbase.sh start --daemon    # Start PocketBase in background
#   ./pocketbase.sh stop              # Stop running background PocketBase
#   ./pocketbase.sh restart           # Stop, then start PocketBase
#   ./pocketbase.sh status            # Check PocketBase health and status
#   ./pocketbase.sh provision         # Re-apply schema definitions to SQLite DB
#   ./run.sh pb <cmd>                 # Short orchestrator route for ./pocketbase.sh
#
# There is no root-level `pb.sh` alias script: a short alias in the project root collided
# with the full name under shell tab-completion, and `./run.sh pb` reaches the same runner.
# ==============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

# Shared process-lifecycle primitives (pidfiles, liveness, LISTEN-only port probe,
# graceful stop, and the runtime-directory policy). Sourced so a sixth service inherits
# them instead of copying them.
# shellcheck source=runner_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/runner_lib.sh"

# PocketBase is an Infrastructure Service: exactly one runs per machine, every worktree
# shares it, so its state anchors at the git common root. That used to be re-derived
# here; it is the library's stated policy now, so an un-symlinked worktree behaves the
# same way instead of half-splitting.
COMMON_ROOT="$(runner_common_root "${ROOT_DIR}")"
RUNTIME_DIR="$(runner_runtime_dir infra "${ROOT_DIR}")"

# Seconds to wait for a cooperative exit before escalating to SIGKILL.
PB_STOP_TIMEOUT_SEC="${PB_STOP_TIMEOUT_SEC:-10}"

# One config read, through the library.
PB_DATA_DIR="${PB_DATA_DIR:-$(runner_config_value pocketbase_data_dir "${COMMON_ROOT}/.boss_agent/pb_data" pb_data_dir)}"
if [[ "${PB_DATA_DIR}" != /* ]]; then
    PB_DATA_DIR="${COMMON_ROOT}/${PB_DATA_DIR}"
fi
PB_PUBLIC_DIR="${PB_PUBLIC_DIR:-${ROOT_DIR}/pb_public}"
PB_HTTP="${PB_HTTP:-0.0.0.0:8090}"
PID_FILE="${RUNTIME_DIR}/pocketbase.pid"
LOG_FILE="${RUNTIME_DIR}/pocketbase.log"

PB_BIN="$(runner_find_binary pocketbase /opt/homebrew/bin/pocketbase /usr/local/bin/pocketbase)"

# The one description of the PocketBase State Stream broker (ticket #476). A runner fills
# it in once; the lifecycle itself — what is signalled, in what order, with which budget,
# and which events are recorded — belongs to the library. `--http` can move the port at
# launch time, so `cmd_start` re-describes the service after it parses its arguments.
configure_service() {
    runner_service_reset
    RUNNER_SERVICE_LABEL="PocketBase"
    RUNNER_SERVICE_NAME="PocketBase"
    RUNNER_SERVICE_PID_FILE="${PID_FILE}"
    RUNNER_SERVICE_LOG_FILE="${LOG_FILE}"
    RUNNER_SERVICE_PORT="${PB_HTTP##*:}"
    RUNNER_SERVICE_HEALTH_URL="http://${PB_HTTP}/api/health"
    RUNNER_SERVICE_ENDPOINT="http://${PB_HTTP}"
    RUNNER_SERVICE_STOP_TIMEOUT_SEC="${PB_STOP_TIMEOUT_SEC}"
    RUNNER_SERVICE_STOP_DETAIL="PocketBase State Stream broker"
    RUNNER_SERVICE_STOP_COMPLETE_DETAIL="PocketBase State Stream broker stopped"
    RUNNER_SERVICE_RESTART_DETAIL="PocketBase State Stream broker"
    RUNNER_SERVICE_RESTART_COMPLETE_DETAIL="PocketBase State Stream broker back online"
    # `pocketbase serve` re-execs, so a process can outlive the launcher's PID.
    RUNNER_SERVICE_RESIDUAL_PATTERN="pocketbase serve --http ${PB_HTTP}"
    # `cmd_start` exits from inside its own health check when the broker is already up.
    RUNNER_SERVICE_RESTART_ISOLATED="1"
    RUNNER_SERVICE_STOP_BANNER="🛑 Stopping local PocketBase instance..."
    RUNNER_SERVICE_IDLE_MESSAGE="No running PocketBase process found."
    RUNNER_SERVICE_STOPPED_MESSAGE="PocketBase stopped successfully."
    RUNNER_SERVICE_STATUS_CHECKING="Checking PocketBase status..."
    RUNNER_SERVICE_STATUS_UP="🟢 PocketBase is RUNNING and HEALTHY at http://${PB_HTTP}/api/health"
    RUNNER_SERVICE_STATUS_DOWN="🔴 PocketBase is NOT REACHABLE at http://${PB_HTTP}/api/health"
    RUNNER_SERVICE_STATUS_PID_LINE="   Process PID : {pid}"
    RUNNER_SERVICE_STATUS_LOG_LINE="   Log file    : {log}"
    RUNNER_SERVICE_STATUS_STALE_WARNING="   (Warning: Stale process {pid} detected)"
}

configure_service

run_provisioner() {
    local TARGET_DB="${1:-${PB_DATA_DIR}/data.db}"
    if command -v uv >/dev/null 2>&1; then
        uv run python3 src/boss_agent/broker/provisioner.py "${TARGET_DB}"
    elif command -v python3 >/dev/null 2>&1; then
        python3 src/boss_agent/broker/provisioner.py "${TARGET_DB}"
    fi
}

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
            --http)
                PB_HTTP="$2"
                shift 2
                ;;
            --dir)
                PB_DATA_DIR="$2"
                shift 2
                ;;
            *)
                shift
                ;;
        esac
    done

    if [[ -z "${PB_BIN}" ]]; then
        echo "❌ Error: 'pocketbase' binary not found." >&2
        echo "💡 Install PocketBase via: brew install pocketbase" >&2
        exit 1
    fi

    # `--http` may have moved the port; the service description follows it.
    configure_service
    local HEALTH_URL="http://${PB_HTTP}/api/health"

    # Check if already running
    if runner_service_healthy ""; then
        local RUNNING_PID
        RUNNING_PID="$(runner_service_resolve_pid)"
        if [[ ${DAEMON} -eq 1 ]]; then
            echo "ℹ️ PocketBase is already running in background (PID: ${RUNNING_PID:-unknown}) at ${HEALTH_URL}"
            exit 0
        else
            runner_attached_logs "${RUNNING_PID:-unknown}" "${LOG_FILE}" "PocketBase" "http://${PB_HTTP}" "${PID_FILE}"
            exit 0
        fi
    fi

    mkdir -p "${PB_DATA_DIR}"

    # Initialize PocketBase SQLite database structure offline if not yet existing
    local DB_FILE="${PB_DATA_DIR}/data.db"
    if [[ ! -f "${DB_FILE}" ]]; then
        echo "📦 Initializing fresh PocketBase SQLite database structure..."
        "${PB_BIN}" migrate up --dir "${PB_DATA_DIR}" >/dev/null 2>&1 || true
    fi

    # Pre-provision SQLite schema and default seeds BEFORE starting server
    # so that PocketBase loads all collections and seeds into memory at boot
    echo "🔧 Pre-provisioning PocketBase SQLite schema and collections..."
    run_provisioner "${DB_FILE}"

    if [[ ${DAEMON} -eq 1 ]]; then
        echo "🚀 Starting PocketBase in background on http://${PB_HTTP}..."
        "${PB_BIN}" serve --http "${PB_HTTP}" --dir "${PB_DATA_DIR}" --publicDir "${PB_PUBLIC_DIR}" >> "${LOG_FILE}" 2>&1 &
        local PID=$!
        echo "${PID}" > "${PID_FILE}"

        # Wait for health check
        for _ in {1..30}; do
            if curl -s -f "${HEALTH_URL}" >/dev/null 2>&1; then
                echo "✅ PocketBase successfully started in background (PID: ${PID})"
                echo "   Dashboard : http://${PB_HTTP}/_/"
                echo "   Portal    : http://${PB_HTTP}/ (Auto-redirects to Admin Dashboard)"
                echo "   REST API  : http://${PB_HTTP}/api/"
                echo "   Log File  : ${LOG_FILE}"
                exit 0
            fi
            sleep 0.2
        done
        echo "⚠️ PocketBase failed to respond to health check within 6s. Check ${LOG_FILE}" >&2
        exit 1
    else
        echo "🚀 Starting PocketBase on http://${PB_HTTP}..."
        echo "   Data directory : ${PB_DATA_DIR}"
        echo "   Public portal  : ${PB_PUBLIC_DIR}"
        echo "   Dashboard      : http://${PB_HTTP}/_/"
        echo "   Portal         : http://${PB_HTTP}/ (Auto-redirects to Admin Dashboard)"
        echo "   REST API       : http://${PB_HTTP}/api/"
        echo "   Log File       : ${LOG_FILE}"
        echo "   Press Ctrl+C to stop."
        echo ""

        "${PB_BIN}" serve --http "${PB_HTTP}" --dir "${PB_DATA_DIR}" --publicDir "${PB_PUBLIC_DIR}" >> "${LOG_FILE}" 2>&1 &
        local PID=$!
        echo "${PID}" > "${PID_FILE}"

        # Handle shutdown on Ctrl+C for foreground mode: graceful SIGTERM then wait for process to checkpoint WAL
        # `runner_watch_detach` first: the watch below forks its own `tail`, and a background job
        # in a non-interactive shell ignores SIGINT — only an explicit signal takes it down.
        trap 'runner_watch_detach; echo -e "\n🛑 Stopping PocketBase (PID: '"${PID}"')..."; kill '"${PID}"' 2>/dev/null || true; wait '"${PID}"' 2>/dev/null || true; rm -f '"${PID_FILE}"'; exit 0' INT TERM

        # The same liveness watch the attach path uses (ticket #425). A bare `tail -n 0 -f`
        # follows a *file*, and a broker stopped from another terminal neither truncates,
        # rotates, nor removes its log — so the terminal kept a live cursor on an inert file
        # and could not tell a busy State Stream broker from one killed thirty seconds ago.
        # The pidfile is what lets a restart from another terminal be announced as one.
        runner_watch_log_stream "${PID}" "${LOG_FILE}" "PocketBase" 0 "${PID_FILE}"
    fi
}

cmd_restart() {
    echo "🔄 Restarting local PocketBase instance..."
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
    provision)
        echo "🔧 Provisioning PocketBase SQLite schema..."
        run_provisioner
        echo "✅ Schema provisioning complete."
        ;;
    *)
        cmd_start "$@"
        ;;
esac
