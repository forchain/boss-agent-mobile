# Agent Guidelines

## Agent skills

### Issue tracker

GitHub issues and PRDs tracked via `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical triage roles mapped to repo labels. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout at repo root (`GLOSSARY.md` + `docs/adr/`); align terms before exploring. See `docs/agents/domain.md`.

### Demo and multimedia assets

Host demo media via GitHub attachments (`user-attachments`) to maintain zero git history bloat. See `docs/agents/demo-assets.md`.

### Test guidelines

Three-tier testing: iterate on the single touched file, verify the fast unit tier (`uv run --extra dev pytest`) before completion, and opt into E2E (`tests/e2e`) or device (`-m live`) tiers only when changes are end-to-end. See `docs/agents/testing.md`.

### Git workflow and PR rules

Descriptive branch naming (`forchain/<name>` or `feat/<name>`), single PR per worktree discipline, and explicit issue-closing keywords (`Closes #123`). See `docs/agents/git-workflow.md`.

## Quick Commands

- **Unit Tests (Pre-completion gate)**: `uv run --extra dev pytest`
- **Unit Budget Gate**: `uv run --extra dev python scripts/check_fast_tier_budget.py`
- **Types Sync Guard**: `uv run python scripts/generate_dashboard_types.py --check`
- **Static Analysis & Formatting**: `uv run --extra dev ruff check && uv run --extra dev ruff format --check`
- **Web Dashboard Check**: `npm --prefix web run check && npm --prefix web test`
- **System Doctor**: `./doctor.sh`


