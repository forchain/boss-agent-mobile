#!/usr/bin/env bash
# ==============================================================================
# Boss Agent Mobile - Main Live Runner Script
# ==============================================================================
# Convenience entrypoint to execute the live automation harness with full
# config-first support and optional CLI argument overrides.
#
# Usage:
#   ./run.sh
#   ./run.sh --keyword "AI Agent"
#   ./run.sh --resume /path/to/resume.pdf --preview-timeout 5
# ==============================================================================

set -euo pipefail

# Ensure working directory is the repo root
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

# Pre-flight environment check
if command -v uv >/dev/null 2>&1; then
    RUNNER=(uv run python3)
elif command -v python3 >/dev/null 2>&1; then
    RUNNER=(python3)
else
    echo "❌ Error: Neither 'uv' nor 'python3' was found in PATH." >&2
    exit 1
fi

ensure_pocketbase() {
    if curl -s -f http://127.0.0.1:8090/api/health >/dev/null 2>&1; then
        echo "✅ PocketBase State Stream is already active on http://127.0.0.1:8090"
        return 0
    fi

    local PB_BIN=""
    if command -v pocketbase >/dev/null 2>&1; then
        PB_BIN="pocketbase"
    elif [[ -x "/opt/homebrew/bin/pocketbase" ]]; then
        PB_BIN="/opt/homebrew/bin/pocketbase"
    elif [[ -x "/usr/local/bin/pocketbase" ]]; then
        PB_BIN="/usr/local/bin/pocketbase"
    fi

    if [[ -n "${PB_BIN}" ]]; then
        echo "🚀 Auto-starting PocketBase State Stream on http://127.0.0.1:8090..."
        mkdir -p .boss_agent/pb_data
        "${PB_BIN}" serve --http 127.0.0.1:8090 --dir .boss_agent/pb_data > .boss_agent/pocketbase.log 2>&1 &
        local PB_PID=$!

        for _ in {1..25}; do
            if curl -s -f http://127.0.0.1:8090/api/health >/dev/null 2>&1; then
                echo "✅ PocketBase State Stream successfully booted (PID: ${PB_PID})"
                trap "echo '🛑 Stopping PocketBase...'; kill ${PB_PID} 2>/dev/null || true" EXIT INT TERM
                return 0
            fi
            sleep 0.2
        done
        echo "⚠️ PocketBase startup timed out, continuing in local fallback mode."
    else
        echo "⚠️ PocketBase binary not found. Web console will run in local fallback mode."
    fi
}

if [[ "${1:-}" == "--web" || "${1:-}" == "web" ]]; then
    shift || true
    ensure_pocketbase
    echo "🌐 Starting Boss Agent Mobile SvelteKit Web Dashboard on http://127.0.0.1:5173..."
    npm --prefix web run dev "$@"
    exit $?
fi

if [[ "${1:-}" == "--worker" || "${1:-}" == "worker" ]]; then
    shift || true
    ensure_pocketbase
    echo "🤖 Starting Boss Agent Mobile Out-of-Process Worker Daemon..."
    exec "${RUNNER[@]}" scripts/worker.py "$@"
fi

echo "🚀 Launching Boss Agent Mobile Live Harness..."
exec "${RUNNER[@]}" scripts/run_live_test.py "$@"



