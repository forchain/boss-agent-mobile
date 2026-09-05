#!/usr/bin/env bash
# ==============================================================================
# Boss Agent Mobile - PocketBase Standalone Runner
# ==============================================================================
# Manages local PocketBase State Stream instance with persistent logging and
# auto-attach to live log stream if already running.
#
# Usage:
#   ./pocketbase.sh                   # Start or attach to PocketBase in foreground (alias: ./pb.sh)
#   ./pocketbase.sh start             # Start or attach to PocketBase in foreground
#   ./pocketbase.sh start --daemon    # Start PocketBase in background
#   ./pocketbase.sh stop              # Stop running background PocketBase
#   ./pocketbase.sh status            # Check PocketBase health and status
#   ./pocketbase.sh provision         # Re-apply schema definitions to SQLite DB
#   ./pb.sh <cmd>                     # Short symlink alias for ./pocketbase.sh
# ==============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

GIT_COMMON_DIR="$(git rev-parse --git-common-dir 2>/dev/null || true)"
if [[ -n "${GIT_COMMON_DIR}" ]]; then
    COMMON_ROOT="$(cd "${GIT_COMMON_DIR}/.." && pwd)"
else
    COMMON_ROOT="${ROOT_DIR}"
fi

mkdir -p "${COMMON_ROOT}/.boss_agent"

if [[ -z "${PB_DATA_DIR:-}" && -f "config/settings.local.yaml" ]]; then
    PB_DATA_DIR="$(grep -E "^[[:space:]]*(pocketbase_data_dir|pb_data_dir):" config/settings.local.yaml 2>/dev/null | awk '{print $2}' | tr -d '"' | tr -d "'" || true)"
fi
if [[ -z "${PB_DATA_DIR:-}" && -f "config/settings.yaml" ]]; then
    PB_DATA_DIR="$(grep -E "^[[:space:]]*(pocketbase_data_dir|pb_data_dir):" config/settings.yaml 2>/dev/null | awk '{print $2}' | tr -d '"' | tr -d "'" || true)"
fi
PB_DATA_DIR="${PB_DATA_DIR:-${COMMON_ROOT}/.boss_agent/pb_data}"
if [[ "${PB_DATA_DIR}" != /* ]]; then
    PB_DATA_DIR="${COMMON_ROOT}/${PB_DATA_DIR}"
fi
PB_PUBLIC_DIR="${PB_PUBLIC_DIR:-${ROOT_DIR}/pb_public}"
PB_HTTP="${PB_HTTP:-0.0.0.0:8090}"
PID_FILE="${COMMON_ROOT}/.boss_agent/pocketbase.pid"
LOG_FILE="${COMMON_ROOT}/.boss_agent/pocketbase.log"

find_pb_binary() {
    if command -v pocketbase >/dev/null 2>&1; then
        echo "pocketbase"
    elif [[ -x "/opt/homebrew/bin/pocketbase" ]]; then
        echo "/opt/homebrew/bin/pocketbase"
    elif [[ -x "/usr/local/bin/pocketbase" ]]; then
        echo "/usr/local/bin/pocketbase"
    else
        echo ""
    fi
}

PB_BIN="$(find_pb_binary)"

run_provisioner() {
    local TARGET_DB="${1:-${PB_DATA_DIR}/data.db}"
    if command -v uv >/dev/null 2>&1; then
        uv run python3 src/boss_agent/broker/provisioner.py "${TARGET_DB}"
    elif command -v python3 >/dev/null 2>&1; then
        python3 src/boss_agent/broker/provisioner.py "${TARGET_DB}"
    fi
}

get_running_pb_pid() {
    if [[ -f "${PID_FILE}" ]]; then
        local PID
        PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
        if [[ -n "${PID}" ]] && ps -p "${PID}" >/dev/null 2>&1; then
            echo "${PID}"
            return 0
        fi
    fi

    # Fallback to lsof on configured port
    local PORT="${PB_HTTP##*:}"
    local PORT_PID
    PORT_PID="$(lsof -ti ":${PORT}" 2>/dev/null | head -n 1 || true)"
    if [[ -n "${PORT_PID}" ]]; then
        echo "${PORT_PID}" > "${PID_FILE}"
        echo "${PORT_PID}"
        return 0
    fi
    echo ""
}

attach_logs() {
    local PID="$1"
    local ENDPOINT="http://${PB_HTTP}"

    echo "ℹ️ PocketBase is already running (PID: ${PID}) at ${ENDPOINT}"
    echo "👀 Attaching to live log stream (${LOG_FILE})... (Press Ctrl+C to detach)"
    echo "----------------------------------------------------------------------"

    # Trap Ctrl+C to exit cleanly without killing the background PocketBase daemon
    trap 'echo -e "\n👋 Detached from PocketBase logs (PocketBase is still running in background)."; exit 0' INT TERM

    if [[ ! -f "${LOG_FILE}" ]]; then
        touch "${LOG_FILE}"
    fi

    tail -n 30 -f "${LOG_FILE}"
}

cmd_status() {
    echo "🔍 Checking PocketBase status..."
    local HEALTH_URL="http://${PB_HTTP}/api/health"
    local PID
    PID="$(get_running_pb_pid)"

    if curl -s -f "${HEALTH_URL}" >/dev/null 2>&1; then
        echo "🟢 PocketBase is RUNNING and HEALTHY at ${HEALTH_URL}"
        if [[ -n "${PID}" ]]; then
            echo "   Process PID : ${PID}"
        fi
        echo "   Log file    : ${LOG_FILE}"
        return 0
    else
        echo "🔴 PocketBase is NOT REACHABLE at ${HEALTH_URL}"
        if [[ -n "${PID}" ]]; then
            echo "   (Warning: Stale process ${PID} detected)"
            rm -f "${PID_FILE}"
        fi
        return 1
    fi
}

cmd_stop() {
    echo "🛑 Stopping local PocketBase instance..."
    local STOPPED=0
    local PID
    PID="$(get_running_pb_pid)"

    if [[ -n "${PID}" ]]; then
        kill "${PID}" 2>/dev/null || true
        local WAITED=0
        while ps -p "${PID}" >/dev/null 2>&1 && [[ ${WAITED} -lt 50 ]]; do
            sleep 0.1
            WAITED=$((WAITED + 1))
        done
        if ps -p "${PID}" >/dev/null 2>&1; then
            echo "⚠️ PocketBase did not shut down gracefully within 5s, sending SIGKILL..."
            kill -9 "${PID}" 2>/dev/null || true
            sleep 0.2
        fi
        STOPPED=1
    fi
    rm -f "${PID_FILE}"

    # Cleanup any lingering process matching pocketbase serve on PB_HTTP
    local LINGER_PIDS
    LINGER_PIDS="$(pgrep -f "pocketbase serve --http ${PB_HTTP}" 2>/dev/null || true)"
    if [[ -n "${LINGER_PIDS}" ]]; then
        kill ${LINGER_PIDS} 2>/dev/null || true
        sleep 0.5
        kill -9 ${LINGER_PIDS} 2>/dev/null || true
        STOPPED=1
    fi

    if [[ ${STOPPED} -eq 1 ]]; then
        echo "✅ PocketBase stopped successfully."
    else
        echo "ℹ️ No running PocketBase process found."
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

    local HEALTH_URL="http://${PB_HTTP}/api/health"

    # Check if already running
    if curl -s -f "${HEALTH_URL}" >/dev/null 2>&1; then
        local RUNNING_PID
        RUNNING_PID="$(get_running_pb_pid)"
        if [[ ${DAEMON} -eq 1 ]]; then
            echo "ℹ️ PocketBase is already running in background (PID: ${RUNNING_PID:-unknown}) at ${HEALTH_URL}"
            exit 0
        else
            attach_logs "${RUNNING_PID:-unknown}"
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
        trap 'echo -e "\n🛑 Stopping PocketBase (PID: '"${PID}"')..."; kill '"${PID}"' 2>/dev/null || true; wait '"${PID}"' 2>/dev/null || true; rm -f '"${PID_FILE}"'; exit 0' INT TERM

        tail -n 0 -f "${LOG_FILE}"
    fi
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
    provision)
        echo "🔧 Provisioning PocketBase SQLite schema..."
        run_provisioner
        echo "✅ Schema provisioning complete."
        ;;
    *)
        cmd_start "$@"
        ;;
esac
