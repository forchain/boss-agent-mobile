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

if [[ "${1:-}" == "--web" || "${1:-}" == "web" ]]; then
    shift || true
    echo "🌐 Starting Boss Agent Mobile Web Dashboard on http://127.0.0.1:8000/dashboard..."
    exec "${RUNNER[@]}" -m uvicorn boss_agent.api.app:create_app --factory --host 127.0.0.1 --port "${PORT:-8000}" "$@"
fi

echo "🚀 Launching Boss Agent Mobile Live Harness..."
exec "${RUNNER[@]}" scripts/run_live_test.py "$@"

