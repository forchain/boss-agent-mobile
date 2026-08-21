#!/usr/bin/env bash
# ==============================================================================
# Boss Agent Mobile - Worktree Initializer Launcher
# ==============================================================================
# Convenient root entrypoint to initialize synchronized git worktrees with
# automated main sync, rebasing, and shared local configuration symlinks.
#
# Usage:
#   ./init_worktree.sh <name> [--branch <branch>] [--path <custom_path>]
#   ./init_worktree.sh <name> --no-rebase
#   ./init_worktree.sh <name> --json
#   ./init_worktree.sh <name> --dry-run
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
