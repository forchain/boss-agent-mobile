# 0013. Deepen Candidate Screener, Mobile Job Feed Pipeline, and Job Record Store

We decided to consolidate candidate screening into a deep `CandidateScreener` module, encapsulate mobile feed pagination and description expansion into a unified `JobFeedPipeline`, and extract a dedicated `JobRecordStore` repository seam from the State Stream Broker.

## Context
As the Boss 直聘 automation grew, candidate screening and feed navigation accumulated significant architectural friction (Spec #231):
- `ScrapeJobsHandler` and `AutoApplyHandler` duplicated over 1,300 lines of procedural glue code (search entry, filter dialog manipulation, coordinate parsing, boundary checking, and cooldown evaluation).
- `AutoApplyHandler` lacked feed pagination and only evaluated single screens, while `ScrapeJobsHandler` lacked multi-stage semantic screening.
- Candidate screening rules (card keywords, App-Enforced Filters, Whitelist Relaxation, and JD semantic checks) were scattered across procedural handlers, shallow graph wrappers (`GreetingDrafterAgent`), and helper functions.
- `BaseTaskBroker` grew into a 26-method monolithic interface mixing task execution leases with domain entity queries, cool-down math, and direct-hire company exclusion pools.

## Decision
1. **Unified Candidate Screener Module (`CandidateScreener`)**:
   - Provide a clean two-method interface:
     - `evaluate_card(card, policy) -> CardScreeningVerdict`
     - `evaluate_job(card, jd_text, profile, policy, prompt) -> JobEvaluationResult`
   - Encapsulate zero-token keyword matching, App-Enforced Filters, Whitelist Relaxation, JD semantic blacklist checks, and living Greeting Prompt drafting in one cohesive module.
   - Retire shallow pass-through agent wrappers.
2. **Deep Mobile Job Feed Pipeline (`JobFeedPipeline`)**:
   - Encapsulate Two-Anchor Search Entry, bounded Back-only recovery, viewport card scanning, call-to-action button inspection (`btn_chat`), instant historical backout, inline description hotspot expansion, and bottom boundary detection.
   - Handlers become declarative dispatchers configuring target action (`save_jd` vs `auto_apply`) and greeting limit degradation.
3. **Dedicated Job Record Store Seam (`JobRecordStore`)**:
   - Extract job persistence, deduplication fingerprints, direct-hire company exclusion pools, cool-down window expiry, and daily quota counting from `BaseTaskBroker`.
   - Confine `BaseTaskBroker` to task lifecycle, lease heartbeats, and realtime execution logs.

## Trade-offs
- Moving feed pagination into a unified pipeline requires thorough unit and mock-driver testing to ensure both scraping and auto-apply invariants (quota limits, cancellation checks, error backouts) remain strictly honored.
- Retaining backwards-compatibility wrappers on `BaseTaskBroker` during the transition guarantees existing worker daemons remain operational while migrating callers to `JobRecordStore`.
EOF
