#!/usr/bin/env bash
# ==============================================================================
# Boss Agent Mobile - SvelteKit Web Dashboard Runner
# ==============================================================================
# Starts the full-stack SvelteKit Web Dashboard on http://127.0.0.1:5173 with
# strict PocketBase pre-flight health gate.
#
# Usage:
#   ./web.sh
#   POCKETBASE_URL=http://192.168.1.100:8090 ./web.sh
# ==============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

# Check Node / npm environment
if ! command -v npm >/dev/null 2>&1; then
    echo "❌ Error: 'npm' is not installed or not in PATH." >&2
    exit 1
fi

# Resolve PocketBase URL
POCKETBASE_URL="${POCKETBASE_URL:-http://127.0.0.1:8090}"
HEALTH_URL="${POCKETBASE_URL%/}/api/health"

echo "🔍 Checking PocketBase State Stream health at ${HEALTH_URL}..."
if ! curl -s -f "${HEALTH_URL}" >/dev/null 2>&1; then
    echo "❌ Error: PocketBase is not reachable at ${HEALTH_URL}" >&2
    echo "" >&2
    echo "💡 PocketBase State Stream broker must be running first:" >&2
    echo "   - Local PocketBase: run './pb.sh' in another terminal" >&2
    echo "   - Remote PocketBase: export POCKETBASE_URL=\"http://<remote-ip>:<port>\"" >&2
    echo "" >&2
    exit 1
fi

echo "✅ PocketBase State Stream is healthy (${POCKETBASE_URL})"
echo "🌐 Starting Boss Agent Mobile SvelteKit Web Dashboard on http://127.0.0.1:5173..."
exec npm --prefix web run dev "$@"
