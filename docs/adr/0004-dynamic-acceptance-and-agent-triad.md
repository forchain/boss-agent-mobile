# 0004. Dynamic Acceptance Baseline and Agent Triad

We decided to use a self-contained root `ACCEPTANCE.md` as the single source of truth and enforce an Agent Triad model (Dev, Test, Acceptance) with strictly isolated contexts.

## Context
Complex agent workflows suffer from context window saturation, confirmation bias, and loss of requirements across agent restarts. We need a reliable mechanism to preserve project truth and ensure high-integrity verification.

## Decision
1. `ACCEPTANCE.md` at repo root records all scope requirements, executable Gherkin-style acceptance tests, runtime anomaly logs, and verification evidence. Any runtime quirks or requirement changes must be recorded here immediately.
2. The Agent Triad separates responsibilities:
   - Dev Agent implements code and maintains unit tests.
   - Test Agent runs emulator integration tests and logs anomalies.
   - Acceptance Agent runs independent end-to-end verification with a clean context before signing off.
