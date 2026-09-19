# 0010. Single Living Greeting Prompt Replaces Greeting Style Rules Memory

Long-term greeting memory was a growing collection of condition-action Greeting Style Rules distilled from per-job critiques, stored as a YAML list and concatenated into the system prompt. We decided to retire it in favor of one editable **Greeting Prompt** document (Markdown in the Configuration Realm, `config/greeting_prompt.local.md`), seeded from the hardcoded writing principles, refined by a whole-document LLM rewrite that a human approves as a before/after diff. The candidate's real need is to inspect and correct "the final settled memory" as one coherent text; a rule list made that verification, deduplication, and correctness auditing end up more complex than the problem warranted.

## Considered Options

- **Keep rules, add provenance links to raw critiques** — rejected: raw critiques are not persisted anywhere, and building a comment→rule audit trail adds exactly the complexity the user asked to remove.
- **Rules + `manually_edited` protection flags against re-distillation** — rejected: conflict machinery is unnecessary once memory is a single human-adopted document.
- **Append-only prompt growth (LLM appends one lesson per adoption)** — rejected: the tail grows back into the unmanageable rule list we just deleted; only whole-document rewrites keep prose coherent.
- **Coexistence of rules and prompt during transition** — rejected: two memory surfaces means "which wins?" questions; the switch is cheap because no `greeting_rules.local.yaml` user data exists anywhere, so there is nothing to migrate.

## Consequences

- The full writing guidance (persona + anti-template iron laws) moves out of hardcoded Python into the seeded default document; code keeps only structural scaffolding (candidate profile interpolation, JSON output contract). Fixing a bad instruction no longer requires a code change.
- TS and Python config-root resolution must agree (git common root), otherwise the Web UI and the headless LangGraph automation read different files in linked worktrees.
- Prompt refinement fails loudly on LLM errors instead of fabricating a synthetic rule as the old `distill` path did — deliberate deviation, to keep the memory surface trustworthy.
- No history of prompt versions: the adopt-time diff is the only review gate, and git does not track `*.local` files. Accepted deliberately.
