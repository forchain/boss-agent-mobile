# Python Architecture Iron Rules

Single source of truth for Python Hexagonal / Clean architecture in this repo.
- **Designing or editing modules**: apply every rule in [Reference: Iron Rules](#reference-iron-rules) and verify against [Compliance Gate](#compliance-gate).
- **Refactoring existing code**: execute [Workflow: Two-Phase Refactor](#workflow-two-phase-refactor) in order.

## Workflow: Two-Phase Refactor

### Step 1: Diagnose & Blueprint (Hard Gate)

1. Inspect the target codebase and directory tree; list every coupling violation mapped to the rule it breaks.
2. Produce the complete target directory tree obeying [3. Directory Layout](#3-directory-layout).
3. Produce four explicit inventories:
   - **Domain models** (core entities and value objects)
   - **Domain exceptions**
   - **Use Cases** (Application entrypoints)
   - **Ports** (abstract `Protocol` or `abc.ABC` interfaces)
4. **Pause and wait for explicit approval** of the blueprint before modifying or creating any source code.

**Done when**: every coupling violation is cited against a rule, the complete target tree and all four inventories are delivered, and explicit approval is received.

### Step 2: TDD Layer-by-Layer Implementation

Execute strictly in dependency order: `Domain -> Application -> Adapters -> Composition Root`.

1. **Domain**: write unit tests for pure business rules and domain exceptions first (red), then implement until green.
2. **Application**: define Ports and write unit tests for Use Cases using in-memory fakes or mocks (red), then implement Use Cases until green.
3. **Adapters**: write unit/contract tests for inbound translation and outbound Port implementations (red), then implement until green.
4. **Composition Root**: wire concrete Adapters into Application Use Cases in the single entrypoint and verify end-to-end wiring.

**Done when**: every module in the approved blueprint is implemented in strict layer order, every module has passing fast in-memory/mock unit tests (zero real DB or network I/O), and every item in [Compliance Gate](#compliance-gate) passes.

## Reference: Iron Rules

### 1. Layering & Dependency Direction

Dependencies point strictly inward (`Adapters -> Application -> Domain`, volatile depends on stable):

| Layer | Stability | Owns | Allowed Imports |
| --- | --- | --- | --- |
| **Domain** | Most stable | Ubiquitous language, core data models, pure business rules, Domain Exceptions | Python stdlib + own Domain types |
| **Application** | Orchestration | Use Cases (workflow orchestration), abstract **Ports** (`Repository`, `Client`, `Store` via `Protocol` or `abc.ABC` + `@abstractmethod`) | Domain + own Application Ports |
| **Adapters** | Most volatile | **Inbound** (FastAPI/Flask routes, CLI): parse requests, call Use Cases, translate Domain Exceptions to external responses (e.g., `HTTPException`). **Outbound**: implement Ports for DB CRUD, third-party APIs/LLMs, file storage. | Application + Domain + external libraries |

### 2. DIP, DI & Composition Root

- **DIP (Dependency Inversion)**: Application defines Port abstractions; Adapters implement them so details depend on abstractions.
- **DI (Dependency Injection)**: Every class accepts its Port dependencies through `__init__` parameters typed to abstractions, keeping concrete instantiation outside business classes.
- **Composition Root**: A single outermost startup entrypoint (e.g., `main.py`) instantiates all concrete Adapters and injects them into Application Use Cases. Swapping a database or external service touches only this file.

### 3. Directory Layout

Separate external technology from core business logic, and aggregate inside by core domain model:

- **`adapters/` (Outside)**: All technology-specific inbound and outbound implementations.
- **`core/` (or `modules/`, Inside)**: Group Use Cases sharing a core domain model into one **Business Aggregate** package (e.g., `core/account/` holding `domain.py`, `use_cases.py`, `interfaces.py` for both transfer and freeze-account). Give independent domain models (e.g., points, notifications) their own sibling packages.
- **Self-evident tree**: Reading the directory tree alone must answer three questions without opening files: where business rules live, which Use Cases exist, and where to swap storage or external services.

## Compliance Gate

Before finishing any architectural change or refactor, verify every item:

- [ ] Domain imports only Python stdlib and Domain types (zero framework, DB, or HTTP imports).
- [ ] Application imports only Domain and Application Ports (zero concrete Adapter imports).
- [ ] Inbound Adapters translate Domain Exceptions into external responses; Outbound Adapters implement Application Ports.
- [ ] Every cross-layer dependency is constructor-injected via Port abstractions.
- [ ] All concrete Adapter instantiations live exclusively in the Composition Root.
- [ ] `adapters/` holds all tech implementations; `core/` groups co-owned Use Cases under Business Aggregate packages.
- [ ] Every module's unit tests run in-memory via fakes/mocks with zero real DB or network calls.
