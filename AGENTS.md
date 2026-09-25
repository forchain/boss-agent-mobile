# Agent Guidelines

## Agent skills

### Issue tracker

GitHub issues tracked via `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical triage roles mapped to repo labels. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout at repo root (`CONTEXT.md` + `docs/adr/`). See `docs/agents/domain.md`.

### Demo and multimedia assets

Zero git history bloat rule: NEVER commit binary media files (`.mp4`, `.mov`, `.gif`, etc.) directly into code branches. See `docs/agents/demo-assets.md`.

### Test guidelines

Three tiers: iterate on the one file you changed, run the fast unit tier
(`uv run --extra dev pytest`) before finishing, and run E2E (`uv run --extra dev pytest
tests/e2e`) only when the change is end-to-end. Device tests require the explicit `live`
marker; E2E runs leave running Worker / Web Dashboard instances alone unless
`BOSS_AGENT_ENFORCE_TEARDOWN=1` is set. See `docs/agents/testing.md`.

