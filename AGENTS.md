# Agent Guidelines

## 铁律 / Iron Rules — Architecture

Act as a senior Python architect applying **DDD**, **Hexagonal Architecture**, and **Clean Architecture**. Every PR writing or refactoring Python code must satisfy all four rules below.

### 1. Layering & Dependency Direction (Volatile → Stable)

Enforce three strict layers with dependencies pointing strictly inward:

- **Domain (innermost, most stable)**: Define ubiquitous language, core data models, pure business rules, and domain exceptions in pure Python. Keep Domain free of framework (FastAPI, SQLAlchemy), database, and network imports.
- **Application (orchestration)**: Implement Use Cases that coordinate Domain models, and define outbound **ports** (`Repository`, `Client`, `Store`) using `abc.ABC` + `@abstractmethod` or `Protocol`. Import only Domain and Application's own ports—never concrete implementations.
- **Adapters (outermost, most volatile)**: Translate external technology to and from internal domain language.
  - **Inbound** (e.g. FastAPI/Flask routes, CLI): Parse external requests, invoke Application Use Cases, and translate domain exceptions into external responses (e.g. `HTTPException`).
  - **Outbound**: Implement Application ports for DB CRUD, third-party APIs/LLMs, and file storage.

### 2. Dependency Inversion & Composition Root

- **DIP**: Define abstract ports in Application; write concrete implementations in Adapters so details depend on abstractions.
- **DI**: Inject all port dependencies through constructors or function parameters; instantiate concrete classes only outside Domain and Application.
- **Composition Root**: Assemble all concrete adapters and inject them into Application Use Cases in a single outermost entry point (e.g. `main.py`). Swapping a database or external service must touch only the Composition Root and the new adapter.

### 3. Directory Structure (Inner/Outer Split + Business Aggregation)

- **Separate outer tech**: Place all framework, I/O, and external-service implementations under `adapters/`.
- **Aggregate inner code by core domain model**: Under `core/` (or `modules/`), co-locate Use Cases that share a core domain model in one business package (e.g. `account/` containing `domain.py`, `use_cases.py`, `interfaces.py` for transfer + freeze), rather than flat technical-layer folders or single-feature silos that duplicate models. Give independent domain models (e.g. points, notifications) their own packages.
- **Self-evident tree**: Make business rules, exposed Use Cases, and adapter swap points identifiable from the directory tree alone without opening files.

### 4. Refactoring Execution Order (Blueprint → Inward-Out TDD)

When refactoring existing Python code, execute in two phases:

1. **Diagnose & Blueprint**: Identify every coupling violation against Rules 1–3, output the complete target directory tree, and list every Domain model, Domain exception, Use Case, and abstract port to extract. Pause for user confirmation of the blueprint before writing code (when working interactively).
2. **Inward-Out TDD**: Refactor strictly in order: `Domain → Application → Adapters → Composition Root`. For each module, write fast unit tests against in-memory/mock adapters first (no live DB or network), then implement until green.

### Architecture Compliance Gate

Verify every item before marking Python changes complete:

- [ ] Domain and Application contain zero framework, DB, network, or concrete adapter imports.
- [ ] All concrete adapter instantiations live solely in the Composition Root.
- [ ] Shared-model Use Cases are aggregated in one business package; external tech lives under `adapters/`.
- [ ] Refactors follow `Domain → Application → Adapters → Composition Root` with in-memory/mock unit tests written first and passing.

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


