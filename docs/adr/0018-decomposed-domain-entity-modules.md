# 0018. Decomposed Domain Entity Modules and Retired Monolith

We decided to retire the monolithic `boss_agent.models` module and decompose it into eight focused, single-responsibility modules with strict dependency hierarchy and zero cycles.

## Context

Originally, `boss_agent.models` accumulated 1,700+ lines defining domain enums, constant dictionaries, regex helpers, UI classification functions, job records, candidate profiles, screening policies, and search configurations.

This monolith caused multiple architectural liabilities:
1. **Unintentional Import Coupling**: Low-level utility functions or pure enums could not be imported without pulling the entire entity graph and schema definitions into memory.
2. **Circular Dependency Hazards**: As components like the broker adapter or configuration realm interacted with domain entities, imports had to be deferred inside functions to avoid cyclic imports with `models.py`.
3. **Blurred Seams**: Persistence concerns, domain calculations, and string parsing primitives lived side-by-side without clear boundaries.

## Decision

1. **Eight Focused Modules**:
   - `boss_agent.enums`: Pure StrEnums and rank order maps (`JobRecordStatus`, `TargetAction`, `TargetTaskType`, `ChatButtonState`, `ChannelPreference`, `AuthStatus`, `STATE_RANK`, `TARGET_ACTION_RANK`, `CHECK_CHAT_ACTION`).
   - `boss_agent.keyword_constants`: Domain keyword dictionaries, regexes, and default thresholds (`PLATFORM_BADGE_MARKERS`, `RECRUITER_SEPARATORS`, `DEFAULT_COMMUNICATION_COOLDOWN_DAYS`, etc.).
   - `boss_agent.identifier_helpers`: Pure string parsers and classification helpers (`clean_job_title`, `normalize_recruiter_name`, `compute_job_fingerprint`, `is_headhunter_agency_name`, etc.) with zero dependencies on entity models.
   - `boss_agent.job_entities`: Job domain models (`JobCardBrief`, `JobRecord`, `JobPosting`).
   - `boss_agent.candidate_entities`: Candidate profile models (`CandidateProfile`).
   - `boss_agent.search_entities`: Search and filter configurations (`SearchConfig`, `FilterConfig`, `SavedSearch`).
   - `boss_agent.screening_policy`: Deterministic card and posting screening policies (`ScreeningPolicy`).
   - `boss_agent.screening_config`: Single-source Configuration Realm file resolution and persistence for screening policies.
   - `boss_agent.entities`: Clean public facade re-exporting the seven core domain entities (`JobCardBrief`, `JobRecord`, `JobPosting`, `CandidateProfile`, `SearchConfig`, `FilterConfig`, `SavedSearch`).

2. **Retire the Monolith**:
   - `src/boss_agent/models.py` is permanently deleted.
   - Attempts to import `boss_agent.models` fail loudly with `ModuleNotFoundError` rather than silently redirecting through a legacy compatibility shim.

## Consequences

- **Strict Layering**: Primitives and identifiers can be imported anywhere without pulling heavy domain dependencies.
- **Fast Unit Isolation**: Unit tests can test individual domain concerns without instantiating or importing unrelated models.
- **Zero Circular Imports**: Module relationships follow a clean acyclic DAG.
