# Architecture Decision Records (ADRs)

This directory documents the significant architectural and design decisions made for the `boss-agent-mobile` project.

We follow the decision record format to capture the context, options considered, decisions, and consequences.

## Decisions Index

| ADR | Title | Status | Date | Summary |
| :--- | :--- | :--- | :--- | :--- |
| [0001](0001-avd-and-appium-bootstrap.md) | Android Virtual Device & Appium for Automation Bootstrap | `ACCEPTED` | 2026-08-16 | Adopted official Android CLI tools, ARM64 Google APIs AVD with HVF acceleration, and Appium UiAutomator2 driver. |
| [0002](0002-framework-domain-split.md) | Split Core Automation Framework and Boss Domain Layer | `ACCEPTED` | 2026-08-16 | Decoupled universal Android automation primitives (`droid_agent_core`) from Boss 直聘 domain logic (`boss_agent`). |
| [0003](0003-phase-1-scope.md) | Phase 1 Scope Baseline | `ACCEPTED` | 2026-08-16 | Bounded Phase 1 to decoupled core, idempotent environment provisioner, and job detail parsing smoke harness. |
| [0004](0004-dynamic-acceptance-and-agent-triad.md) | Dynamic Acceptance Baseline and Agent Triad | `ACCEPTED` | 2026-08-16 | Established `ACCEPTANCE.md` as living source of truth and enforced the Dev/Test/Acceptance Agent triad model. |
| [0005](0005-anti-detection-and-humanized-interaction.md) | Anti-Detection, Humanized Interaction, and Manual Takeover | `ACCEPTED` | 2026-08-17 | Implemented Bézier-curve gesture synthesis, touch jitter, and human takeover interception for anti-bot captchas. |
| [0006](0006-backend-api-worker-and-state-stream.md) | Modular Monolith API, State Stream Broker, and Out-of-Process Worker | `ACCEPTED` | 2026-08-20 | Adopted modular backend service, PocketBase State Stream Broker (`automation_tasks`), and 1:1 dedicated out-of-process worker daemon. |
| [0007](0007-saved-searches-database-and-scheduled-triggers.md) | Database Persisted SavedSearches, Zero-Startup Automation Worker, and Cron Scheduler | `ACCEPTED` | 2026-08-22 | Migrated search presets to PocketBase `saved_searches`, introduced ad-hoc Web dispatch, and added Cron evaluation scheduler (`AutomationScheduler`). |
| [0008](0008-langgraph-resume-lifecycle-and-normalization.md) | LangGraph Resume Lifecycle Orchestration and Structured Profile Document Normalization | `ACCEPTED` | 2026-08-25 | Orchestrated resume parsing and diffing via LangGraph (`ResumeLifecycleGraph`), adopting first-class Markdown `Structured Profile Document` with self-healing normalization. |
| [0009](0009-inline-description-probing-expansion.md) | Inline Job Description Bottom-Right Hotspot Targeting and Early-Stopping Verification | `ACCEPTED` | 2026-08-28 | Targeted bottom-right ClickableSpan touch hotspot on `tv_description` with Bézier jitter to expand collapsed job descriptions without brittle coordinate configs. |
| [0010](0010-single-living-greeting-prompt.md) | Single Living Greeting Prompt Replaces Greeting Style Rules Memory | `ACCEPTED` | 2026-09-02 | Replaced growing rule lists with a single living Markdown `Greeting Prompt` (`config/greeting_prompt.local.md`) refined via whole-document LLM rewriting with human diff approval. |
| [0011](0011-two-anchor-search-entry-back-recovery-and-ui-telemetry.md) | Two-Anchor Search Entry, Back-Only Recovery, and DEBUG UI Telemetry | `ACCEPTED` | 2026-09-21 | Replaced cascading selector probing with a two-anchor entry engine (`et_search` / `img_icon`), bounded hardware Back recovery, and low-level UI telemetry under `droid_agent_core.ui`. |
| [0012](0012-communicated-job-detection-cooldown-and-enterprise-exclusion.md) | Communicated Job Detection, Re-application Cool-down, and Enterprise-Level Direct-Hire Exclusion | `ACCEPTED` | 2026-09-22 | Detected communicated status via `btn_chat`, recorded `platform_historical` as `applied`, suppressed duplicate direct-hire company postings, decoupled greeting quota via `applied_at`, and introduced re-application cool-down (`communication_cooldown_days`). |
| [0013](0013-deepen-screener-feed-pipeline-and-job-store.md) | Deepen Candidate Screener, Mobile Job Feed Pipeline, and Job Record Store | `ACCEPTED` | 2026-09-23 | Consolidated candidate screening into `CandidateScreener`, encapsulated feed navigation into `JobFeedPipeline`, and extracted `JobRecordStore`. |
| [0014](0014-execution-cursor-bounded-check-chat-paging.md) | 仅沟通 Paging Bounded by an Execution Cursor Read Back from Task Records | `SUPERSEDED` | 2026-09-23 | Bounded `CHECK_CHAT` paging by the previous acting run's completion time, derived from SUCCESS task records, with the opening page exempt and card stamps compared at their rendered resolution. Superseded by ADR 0015. |
| [0015](0015-opening-screen-only-check-chat-scan.md) | CHECK_CHAT Reads the Opening Screen and Never Pages | `ACCEPTED` | 2026-09-23 | Removed paging from `CHECK_CHAT` entirely: a run reads the opening screen, re-reads it after each acknowledgment, and ends at `first_screen_exhausted`. Deleted the execution cursor, the card-stamp parser and the broker query behind them. |
| [0016](0016-jingoutong-resilient-recovery-category-tab-and-startup-barrier.md) | 仅沟通 Cleanup: On-Screen Back Recovery, a Dedicated Category Tab, and the Startup Cleanup Barrier | `ACCEPTED` | 2026-09-23 | Added bounded multi-tier navigation for 仅沟通 list recovery, dedicated Web launch tab, and startup barrier. |

## Contributing New Decisions

When proposing a new architectural decision:
1. Copy the naming format: `NNNN-short-hyphenated-title.md` (sequentially numbered, 4 digits).
2. Follow the established sections: Title, Status, Context, Decision, Consequences.
3. Update this index table to reflect the new record.
