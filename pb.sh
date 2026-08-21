#!/usr/bin/env bash
# ==============================================================================
# Boss Agent Mobile - PocketBase Standalone Runner
# ==============================================================================
# Manages local PocketBase State Stream instance with automatic schema provisioning.
#
# Usage:
#   ./pb.sh                   # Start PocketBase in foreground
#   ./pb.sh start             # Start PocketBase in foreground
#   ./pb.sh start --daemon    # Start PocketBase in background
#   ./pb.sh stop              # Stop running background PocketBase
#   ./pb.sh status            # Check PocketBase health and status
#   ./pb.sh provision         # Re-apply schema definitions to SQLite DB
# ==============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

PB_DATA_DIR="${PB_DATA_DIR:-.boss_agent/pb_data}"
PB_HTTP="${PB_HTTP:-127.0.0.1:8090}"
PID_FILE=".boss_agent/pocketbase.pid"
LOG_FILE=".boss_agent/pocketbase.log"

# Locate pocketbase binary
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
    if command -v uv >/dev/null 2>&1; then
        uv run python3 src/boss_agent/broker/provisioner.py "${PB_DATA_DIR}/data.db" >/dev/null 2>&1 || true
    elif command -v python3 >/dev/null 2>&1; then
        python3 src/boss_agent/broker/provisioner.py "${PB_DATA_DIR}/data.db" >/dev/null 2>&1 || true
    fi
}

cmd_status() {
    echo "🔍 Checking PocketBase status..."
    local HEALTH_URL="http://${PB_HTTP}/api/health"
    if curl -s -f "${HEALTH_URL}" >/dev/null 2>&1; then
        echo "🟢 PocketBase is RUNNING and HEALTHY at ${HEALTH_URL}"
        if [[ -f "${PID_FILE}" ]]; then
            local PID="$(cat "${PID_FILE}")"
            echo "   Process PID: ${PID}"
        fi
        return 0
    else
        echo "🔴 PocketBase is NOT REACHABLE at ${HEALTH_URL}"
        if [[ -f "${PID_FILE}" ]]; then
            local PID="$(cat "${PID_FILE}")"
            if ps -p "${PID}" >/dev/null 2>&1; then
                echo "   (Warning: Process with PID ${PID} exists but health check failed)"
            else
                rm -f "${PID_FILE}"
            fi
        fi
        return 1
    fi
}

cmd_stop() {
    echo "🛑 Stopping local PocketBase instance..."
    local STOPPED=0
    if [[ -f "${PID_FILE}" ]]; then
        local PID="$(cat "${PID_FILE}")"
        if ps -p "${PID}" >/dev/null 2>&1; then
            kill "${PID}" 2>/dev/null || true
            sleep 0.5
            kill -9 "${PID}" 2>/dev/null || true
            STOPPED=1
        fi
        rm -f "${PID_FILE}"
    fi

    # Cleanup any lingering process matching pocketbase serve
    pkill -f "pocketbase serve --http ${PB_HTTP}" 2>/dev/null || true

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

    mkdir -p "${PB_DATA_DIR}"
    mkdir -p ".boss_agent"

    # Pre-provision SQLite DB if data.db already exists
    run_provisioner

    local HEALTH_URL="http://${PB_HTTP}/api/health"

    if curl -s -f "${HEALTH_URL}" >/dev/null 2>&1; then
        echo "ℹ️ PocketBase is already running at ${HEALTH_URL}"
        exit 0
    fi

    if [[ ${DAEMON} -eq 1 ]]; then
        echo "🚀 Starting PocketBase in background on http://${PB_HTTP}..."
        "${PB_BIN}" serve --http "${PB_HTTP}" --dir "${PB_DATA_DIR}" > "${LOG_FILE}" 2>&1 &
        local PID=$!
        echo "${PID}" > "${PID_FILE}"

        # Wait for health check
        for _ in {1..30}; do
            if curl -s -f "${HEALTH_URL}" >/dev/null 2>&1; then
                run_provisioner
                echo "✅ PocketBase successfully started in background (PID: ${PID})"
                echo "   Dashboard: http://${PB_HTTP}/_/"
                echo "   REST API:  http://${PB_HTTP}/api/"
                exit 0
            fi
            sleep 0.2
        done
        echo "⚠️ PocketBase failed to respond to health check within 6s. Check ${LOG_FILE}" >&2
        exit 1
    else
        echo "🚀 Starting PocketBase in foreground on http://${PB_HTTP}..."
        echo "   Data directory: ${PB_DATA_DIR}"
        echo "   Dashboard:      http://${PB_HTTP}/_/"
        echo "   REST API:       http://${PB_HTTP}/api/"
        echo "   Press Ctrl+C to stop."
        echo ""
        (sleep 1 && run_provisioner) &
        exec "${PB_BIN}" serve --http "${PB_HTTP}" --dir "${PB_DATA_DIR}"
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
        # Default start with any passed args
        cmd_start "$@"
        ;;
esac
