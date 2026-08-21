#!/usr/bin/env bash
# ==============================================================================
# Boss Agent Mobile - Worktree Initializer Launcher
# ==============================================================================
# Convenient root entrypoint to initialize/synchronize git worktrees with
# automated main sync, rebasing onto latest main, and shared config symlinks.
#
# Usage:
#   ./init_worktree.sh                     # Sync current worktree & rebase on main
#   ./init_worktree.sh <name>              # Initialize/sync named worktree
#   ./init_worktree.sh --dry-run           # Simulate without modifying files
#   ./init_worktree.sh --json              # Output structured JSON for agents
# ==============================================================================

set -euo pipefail

# Ensure working directory is the repo/worktree root
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

# Select runner (prefer uv if installed)
if command -v uv >/dev/null 2>&1; then
    exec uv run python3 "${ROOT_DIR}/scripts/init_worktree.py" "$@"
elif command -v python3 >/dev/null 2>&1; then
    exec python3 "${ROOT_DIR}/scripts/init_worktree.py" "$@"
else
    echo "❌ Error: Neither 'uv' nor 'python3' was found in PATH." >&2
    exit 1
fi
