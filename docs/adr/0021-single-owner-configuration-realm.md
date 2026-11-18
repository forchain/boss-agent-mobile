# 0021. Single-Owner Configuration Realm and the Web-Delegates-to-Python Contract

We decided that `boss_agent.config_realm` is the only reader and writer of the Configuration Realm, that the Web Dashboard delegates every settings and screening read/write to it through one JSON CLI, and that writes use a comment-preserving round-trip serializer.

## Context

`config/settings.local.yaml` was read and written by five owners across two languages: the Python realm, two pass-through Python modules (`settings.py`, `screening_config.py`), and two TypeScript modules (`settings.ts`, `screeningConfig.ts`). The Web writer rebuilt the file from a hardcoded YAML template, so any Python-only key omitted from that template disappeared on the next partial dashboard save — ADR 0016's trade-off note records `run_cleanup_on_startup` nearly vanishing this way. Screening policy had a second Python writer and a second JavaScript YAML parser, held together by a cross-language defaults fixture rather than one loader.

## Decision

1. **One owner.** `boss_agent.config_realm` owns the file chain, the defaults table, environment precedence, coercion, derived paths, the cache, path/cooldown/scheduler/acknowledgment resolvers, screening policy load/persist, and the writable-store resolver. `boss_agent.settings` and `boss_agent.screening_config` are deleted, not shimmed; importing either fails loudly.
2. **One patch engine.** `patch_settings(partial)` writes only the keys it states to the highest-precedence writable local file. It preserves unknown keys, comments and section ordering via `ruamel.yaml` round-trip, deep-merges the nested `chat` block, and refuses to persist masked display values or template placeholders as secrets. Screening policy save and the idempotent company-blacklist append go through the same engine.
3. **One JSON CLI.** `scripts/config_realm_cli.py` exposes `read` (merged settings plus screening policy) and `patch` (partial object). It is transport only: defaults, precedence, validation and error vocabulary stay in Python. The Web layer validates HTTP shape, masks secrets, caches by the file-chain stat signature, and translates realm errors; it never re-derives merge rules or holds a settings template.
4. **One fixture removed.** With a single loader there is nothing to keep in parity, so `config/defaults.fixture.json` and both cross-language parity suites are retired.

## Consequences

- A dashboard save can no longer drop a Python settings key, ending the ADR 0016 hazard.
- Adding a settings key touches one table and one loader; the Web dashboard consumes it without a template edit.
- Operators keep hand-editable YAML: comments and unknown sections survive a round-trip save.
- The Web layer now depends on a Python process for settings I/O; a missing interpreter surfaces as a readable error rather than a stale second implementation.
- `ruamel.yaml` is a new runtime dependency. It replaces hand-rolled line surgery; PyYAML remains for the read path and the flat fallback.
