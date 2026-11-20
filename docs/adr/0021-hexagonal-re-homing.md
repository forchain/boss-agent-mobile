# 0021. Strict Hexagonal Re-homing: Core Business Packages, Adapters, and a Single Composition Root

## Status

`ACCEPTED` — 2026-11-18. Approved contract: spec #443.

## Context

The App Domain Layer (`boss_agent`) had a correct framework/domain split (ADR 0002), decomposed entity modules (ADR 0019), and deep seams for the screener, feed pipeline, Job Record Store, and Chat Triage (ADR 0013/0016/0017). What remained was a flat module pile:

- Abstract interfaces lived inside concrete adapter modules, so importing a port dragged the adapter's HTTP stack into the caller.
- Application services constructed their own LLM clients, page objects, screeners, and classifiers, and read Configuration Realm files and environment settings mid-logic.
- Domain models carried framework and IO concerns: `AutomationTask` was pydantic, `ScreeningPolicy` loaded/saved YAML through classmethods, screening/matching imported `langsmith` and `rich`, and the resume lifecycle mixed LangGraph wiring with business steps.
- Composition knowledge was scattered across eight CLI scripts and several `for_task`/`build_*` helpers.
- The launch contract imported an adapter constant and a settings resolver.

## Decision

Re-home the code into three strictly directed layers, moving rather than rewriting:

1. **`boss_agent/core/`** — Domain + Application. Business packages aggregated by shared domain model (`task`, `job`, `candidate`, `search`, `chat`), each exposing `domain` / `interfaces` / `use_cases`, plus a `shared` kernel for cross-business primitives. Core imports only the standard library, the framework's LLM decision abstraction, and `boss_agent.core.*`. pydantic, langsmith, langgraph, langchain, rich, requests, yaml, Appium/selenium, and `adapters/` are forbidden, enforced by an allowlist-based static scan (`tests/unit/test_core_isolation.py`) that resolves both absolute and relative imports at any nesting depth.
2. **`boss_agent/adapters/`** — everything touched by external technology: inbound (worker daemon + handlers, verification runner), outbound (PocketBase, device page objects and bundles, LLM factory + tracing, Configuration Realm, resume extraction + LangGraph), in-memory implementations, and infrastructure daemons. Helpers shared between adapter implementations (record wire defaults, merge rules, and prompt-loading tolerance) live in `adapters/shared/`, which carries no transport or outbound-adapter dependency (`tests/unit/test_memory_adapter_imports.py`, resolving relative imports across `adapters/memory/` and `adapters/shared/`).
3. **`boss_agent/main.py`** — the single composition root and the only module outside `adapters/` that constructs concrete adapters (`tests/unit/test_composition_confinement.py`). Handlers, verification adapters, and `ResumeMemoryManager` receive injected dependencies from `main.py` rather than resolving LLM clients, device worlds, or stores themselves. CLI scripts keep their frozen paths (process identity: `pgrep -f scripts/worker.py`) and are thin argparse shells over its builders.

Ports move to `core/*/interfaces/`; concrete implementations to adapters. The only new interfaces are `FeedDeviceWorld` (structural Protocols over the page bundle the feed pipeline drives), `BlacklistSink` (Chat Triage persists through an injected port), `ResumeTextSource` (PDF/DOCX extraction), and a declared `RejectionClassifier` Protocol replacing `Any`. Page objects satisfy the device ports structurally — no wrapper classes (ADR 0016 precedent). The Task Broker port keeps only task-stream lifecycle verbs; composed stores remain separately injected (ADR 0013 confinement), and task-stream helpers (`is_task_cancelled`) live in `core/task/use_cases/` typed against `BaseTaskBroker` with `JobRecordStore` passed explicitly where needed.

Domain purity: `AutomationTask` is a plain `@dataclass(kw_only=True)`; the PocketBase adapter owns row↔entity translation and legacy-row tolerance. `ScreeningPolicy` keeps `from_dict`/`to_dict` and its rules but loses load/save/persist classmethods, which became Configuration Realm adapter functions. Core factories receive resolved config values and prompt strings. Tracing decorators, Rich rendering, and LangGraph wiring left core for adapters, preserving span names, run types, and rendering output. The `BrokerError` family keeps its names but is re-homed as port-level exceptions in `core/shared/`; adapters translate transport errors into them.

Migration policy: module bodies and behavior are preserved; retired paths (including the former `boss_agent/broker/` package facade) are deleted and fail loudly with `ModuleNotFoundError` (ADR 0019 precedent); public facade symbol names in `boss_agent/__init__.py` and `boss_agent/entities.py` are preserved and re-pointed; test assertions change only where a constructor now takes an injected dependency or a retired facade/pydantic compatibility shim was removed.

## Deviations from prior pydantic / interim behavior

- **`AutomationTask` extra-key policy and `model_dump()` removal**: Moving `AutomationTask` from pydantic (`extra="ignore"`) to a plain `@dataclass(kw_only=True)` means passing unknown keyword arguments raises `TypeError`, and `model_dump()` was removed after verifying zero production callers remained (pinned by `tests/unit/test_automation_task_contract.py`). The PocketBase adapter maps declared columns explicitly into `AutomationTask` so an unexpected server column never fails row deserialization.

## Resolved ADR tensions

- **ADR 0002** — the owner's "interfaces belong to the application layer" rule conflicts with the framework owning the LLM decision interface. Resolved: the ABC stays in `droid_agent_core` as an owner-approved deviation, because the framework is a stable, separately extractable library and the Model Factory glossary contract names that ABC as the one decision interface.
- **ADR 0006 (amended)** — the inbound-adapter template is not FastAPI. Task admission is the SvelteKit BFF; the Python inbound translation layer is the worker handlers, which map domain outcomes and port exceptions to `HandlerResult` and task telemetry.
- **ADR 0019** — its loud-failure, no-shim retirement precedent applies; its decomposed primitives survive inside `core/shared/` and the aggregate facades.
- **ADR 0013 / 0016 / 0017** — the deep seams and broker/store confinement are preserved; the feed pipeline gains an injected device world without losing its deep interface, following Chat Triage's injected-port precedent.
- **ADR 0008** — the resume lifecycle's substance is preserved; only the LangGraph wiring's home changed.

## Consequences

- Swapping the database, the LLM host, or the device layer touches adapter code and `main.py`, never business rules.
- The directory tree states where rules live, which Use Cases are exposed, and what to replace.
- Importing a port no longer imports an adapter's HTTP stack; the in-memory doubles remain the test seam, and `adapters/memory/` imports neither a transport module nor the PocketBase adapter.
- Static guards prevent silent regression of dependency direction: core's import allowlist, adapter construction confined to `adapters/` and `main.py`, scripts free of concrete wiring, and the framework remaining Boss-free.
- Behavior is unchanged: screening/quota/cooldown/exclusion/depth/greeting-provenance semantics, stop-reason taxonomy, telemetry wording, shutdown markers, trace names, and store contracts are preserved.
- The cost is a one-time repo-wide import churn and a migration that breaks imports of every retired path by design.
