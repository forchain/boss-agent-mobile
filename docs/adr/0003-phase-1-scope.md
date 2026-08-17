# 0003. Phase 1 Scope Baseline

We decided to bound Phase 1 to the decoupled automation framework, the idempotent bootstrap provisioner, and an end-to-end job detail parsing smoke test.

## Context
Mobile automation involves emulator management, driver reliability, and app UI lifecycle quirks. Trying to deliver full AI matching and greeting automation simultaneously creates high cognitive load and context bloat.

## Decision
1. Phase 1 delivers `scripts/bootstrap.py` (idempotent environment provisioner), `droid_agent_core` (reusable framework), and `boss_agent` (smoke harness for app launch, permission dialogs, login check, and job detail parsing).
2. Advanced AI profile matching, automated greeting delivery, and rate-limited dispatch queues are deferred to Phase 2, with interfaces prepared in Phase 1.
