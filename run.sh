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

# Resolve PocketBase URL
POCKETBASE_URL="${POCKETBASE_URL:-http://127.0.0.1:8090}"

check_pocketbase_health() {
    local HEALTH_URL="${POCKETBASE_URL%/}/api/health"
    if ! curl -s -f "${HEALTH_URL}" >/dev/null 2>&1; then
        echo "❌ Error: PocketBase is not reachable at ${HEALTH_URL}" >&2
        echo "" >&2
        echo "💡 PocketBase State Stream broker must be running first:" >&2
        echo "   - Local PocketBase: run './pb.sh' in another terminal" >&2
        echo "   - Remote PocketBase: export POCKETBASE_URL=\"http://<remote-ip>:<port>\"" >&2
        echo "" >&2
        exit 1
    fi
}

# Pre-flight PocketBase health check
check_pocketbase_health

# Command routing
if [[ $# -eq 0 || "${1:-}" == "--worker" || "${1:-}" == "worker" ]]; then
    if [[ "${1:-}" == "--worker" || "${1:-}" == "worker" ]]; then
        shift || true
    fi
    echo "🤖 Starting Boss Agent Mobile Automation Worker Daemon..."
    echo "   PocketBase Broker: ${POCKETBASE_URL}"
    exec "${RUNNER[@]}" scripts/worker.py "$@"
fi

echo "🚀 Launching Boss Agent Mobile Live Harness..."
echo "   PocketBase Broker: ${POCKETBASE_URL}"
exec "${RUNNER[@]}" scripts/run_live_test.py "$@"
