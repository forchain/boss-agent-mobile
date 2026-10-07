"""
boss_agent
==========
Boss 直聘 Android application automation domain layer.

Public symbols resolve lazily (PEP 562).

Importing *any* submodule of a package executes that package's ``__init__`` first, so
this module used to decide the import cost of ``boss_agent.candidate_entities`` — a
leaf whose entire promise is that it is standard-library only. The eager
``from .memory import ...`` below dragged in ``memory``'s ``langsmith``, ``rich`` and
``droid_agent_core.llm``, roughly 1300 modules, and made "the profile entity is pure"
true of the module and false of the import. The eager cost is paid by scripts that only
wanted a dataclass.

So every name is declared in ``_LAZY_EXPORTS`` and resolved on first attribute access
by ``__getattr__``, then cached in the module globals so later accesses skip the hook
entirely. Two properties of the old file are deliberately preserved:

* **Names.** ``__all__`` is unchanged, and each name still resolves to the identical
  object it resolved to before. ``from boss_agent import X`` and ``boss_agent.X`` both
  work, because the import system's attribute fallback and ``__getattr__`` agree.
* **Optional dependencies.** Most imports were wrapped in
  ``contextlib.suppress(ImportError)`` so a missing extra skipped one name and left
  the rest usable. That tolerance now lives in ``__getattr__``: when a symbol's module
  cannot be imported, the attribute form (``boss_agent.X``) raises a clean
  ``AttributeError`` naming the symbol, instead of an ``ImportError`` escaping through
  unrelated attribute access. The ``from boss_agent import X`` form still reports
  ``ImportError: cannot import name 'X'`` -- the import machinery rewrites the
  ``AttributeError`` into that before the caller sees it. That is not a regression: a
  caller written as ``try: from boss_agent import X / except ImportError`` behaves
  exactly as it did against the eager file.

``__getattr__`` deliberately does not handle submodule names. ``from boss_agent import
memory`` must keep working, and it does so through the import system's own submodule
fallback, which only runs because an unknown attribute raises ``AttributeError``.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import-time work for static type checkers only
    from .candidate_entities import CandidateProfile
    from .enums import AuthStatus, ChannelPreference
    from .greeting_prompt import load_greeting_prompt
    from .identifier_helpers import (
        format_recruiter_greeting_prefix,
        is_headhunter_agency_name,
        is_masked_company_name,
        parse_recruiter_title,
    )
    from .job_entities import JobPosting
    from .screening import (
        CandidateScreener,
        CardScreeningVerdict,
        CardVerdictStage,
        JobEvaluationResult,
        JobVerdictStage,
    )
    from .screening_config import (
        append_company_blacklist_entry,
        is_writable_screening_path,
        resolve_writable_screening_config_path,
    )
    from .screening_policy import ScreeningPolicy
    from .search_entities import FilterConfig, SavedSearch, SearchConfig

_LAZY_EXPORTS: dict[str, str] = {
    "AuthStatus": ".enums",
    "BaseBossPage": ".pages",
    "CandidateProfile": ".candidate_entities",
    "CandidateScreener": ".screening",
    "CardScreeningVerdict": ".screening",
    "CardVerdictStage": ".screening",
    "ChannelPreference": ".enums",
    "ChatPage": ".pages",
    "FeedStreamConfig": ".feed_pipeline",
    "FeedStreamResult": ".feed_pipeline",
    "FilterConfig": ".search_entities",
    "FilterDialogPage": ".pages",
    "IndustryFilterDialogPage": ".pages",
    "InMemoryJobRecordStore": ".job_store",
    "JobDetailPage": ".pages",
    "JobEvaluationResult": ".screening",
    "JobFeedPipeline": ".feed_pipeline",
    "JobListPage": ".pages",
    "JobMatchGreetingService": ".matching",
    "JobOutcome": ".feed_pipeline",
    "JobPosting": ".job_entities",
    "JobRecordStore": ".job_store",
    "JobVerdictStage": ".screening",
    "LoginPage": ".pages",
    "MatchGreetingResult": ".matching",
    "PocketBaseJobRecordStore": ".job_store",
    "ProfileNormalizer": ".memory",
    "ResumeLifecycleState": ".graph",
    "ResumeMemoryManager": ".memory",
    "ResumeTextExtractor": ".memory",
    "SavedSearch": ".search_entities",
    "SavedSearchRegistry": ".searches",
    "ScreeningPolicy": ".screening_policy",
    "SearchConfig": ".search_entities",
    "SearchPage": ".pages",
    "SmokeHarness": ".workflows",
    "StartupDialogPage": ".pages",
    "StructuredCandidateProfile": ".candidate_entities",
    "TakeoverHandler": ".workflows",
    "append_company_blacklist_entry": ".screening_config",
    "build_resume_lifecycle_graph": ".graph",
    "ensure_greeting_prefix": ".matching",
    "format_recruiter_greeting_prefix": ".identifier_helpers",
    "get_global_search_registry": ".searches",
    "is_headhunter_agency_name": ".identifier_helpers",
    "is_masked_company_name": ".identifier_helpers",
    "is_writable_screening_path": ".screening_config",
    "load_greeting_prompt": ".greeting_prompt",
    "load_settings": ".settings",
    "parse_recruiter_title": ".identifier_helpers",
    "resolve_git_common_root": ".settings",
    "resolve_pocketbase_data_dir": ".settings",
    "resolve_pocketbase_db_path": ".settings",
    "resolve_pocketbase_url": ".settings",
    "resolve_writable_screening_config_path": ".screening_config",
    "run_resume_lifecycle_graph": ".graph",
}


def __getattr__(name: str) -> object:
    """Resolve a public symbol on first access, then cache it (PEP 562)."""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        value = getattr(importlib.import_module(module_name, __name__), name)
    except ImportError as exc:
        # The eager file swallowed ImportError to tolerate optional dependencies.
        # AttributeError is what a caller can handle per-symbol; ImportError is not,
        # and letting it escape would fail an unrelated `boss_agent.X` access.
        raise AttributeError(
            f"module {__name__!r} cannot provide {name!r}: "
            f"importing {module_name!r} failed with {exc!r}"
        ) from exc
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})


__all__ = [
    "AuthStatus",
    "BaseBossPage",
    "CandidateProfile",
    "CandidateScreener",
    "CardScreeningVerdict",
    "CardVerdictStage",
    "ChannelPreference",
    "ChatPage",
    "FeedStreamConfig",
    "FeedStreamResult",
    "FilterConfig",
    "FilterDialogPage",
    "IndustryFilterDialogPage",
    "InMemoryJobRecordStore",
    "JobDetailPage",
    "JobEvaluationResult",
    "JobFeedPipeline",
    "JobOutcome",
    "JobRecordStore",
    "JobVerdictStage",
    "JobListPage",
    "JobMatchGreetingService",
    "JobPosting",
    "LoginPage",
    "MatchGreetingResult",
    "PocketBaseJobRecordStore",
    "ProfileNormalizer",
    "ResumeLifecycleState",
    "ResumeMemoryManager",
    "ResumeTextExtractor",
    "SavedSearch",
    "SavedSearchRegistry",
    "ScreeningPolicy",
    "SearchConfig",
    "SearchPage",
    "SmokeHarness",
    "StartupDialogPage",
    "StructuredCandidateProfile",
    "TakeoverHandler",
    "append_company_blacklist_entry",
    "is_writable_screening_path",
    "build_resume_lifecycle_graph",
    "get_global_search_registry",
    "is_headhunter_agency_name",
    "is_masked_company_name",
    "load_greeting_prompt",
    "load_settings",
    "resolve_git_common_root",
    "resolve_writable_screening_config_path",
    "resolve_pocketbase_data_dir",
    "resolve_pocketbase_db_path",
    "resolve_pocketbase_url",
    "run_resume_lifecycle_graph",
    "ensure_greeting_prefix",
    "format_recruiter_greeting_prefix",
    "parse_recruiter_title",
]
