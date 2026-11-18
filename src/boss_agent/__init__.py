"""
boss_agent
==========
Boss 直聘 Android application automation domain layer.

Public symbols resolve lazily (PEP 562).

Importing *any* submodule of a package executes that package's ``__init__`` first, so
this module used to decide the import cost of the candidate profile entity — a
leaf whose entire promise is that it is standard-library only. The eager
``from .memory import ...`` below dragged in ``memory``'s ``langsmith``, ``rich`` and
``droid_agent_core.llm``, roughly 1300 modules, and made "the profile entity is pure"
true of the module and false of the import. The eager cost is paid by scripts that only
wanted a dataclass.

So every name is declared in ``_LAZY_EXPORTS`` and resolved on first attribute access
by ``__getattr__``, then cached in the module globals so later accesses skip the hook
entirely. Two properties of the old file are deliberately preserved:

* **Names.** Every name the old file advertised still resolves, and to the identical
  object it resolved to before. ``from boss_agent import X`` and ``boss_agent.X`` both
  work, because the import system's attribute fallback and ``__getattr__`` agree. The
  export set differs only where a later change to the package deliberately retired a
  module, and it did so here rather than by editing this file: ``_LAZY_EXPORTS`` and
  ``__all__`` are kept exactly consistent with each other, so ``__all__`` cannot
  advertise a name the hook does not know how to resolve.
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
    from .adapters.outbound.config.greeting_prompt import load_greeting_prompt
    from .adapters.outbound.config.screening_config import (
        append_company_blacklist_entry,
        is_writable_screening_path,
        resolve_writable_screening_config_path,
    )
    from .core.candidate.domain.candidate_profile import CandidateProfile
    from .core.job.domain.feed_stream import FeedStreamConfig, FeedStreamResult
    from .core.job.domain.job_entities import JobPosting
    from .core.job.domain.screening_policy import ScreeningPolicy
    from .core.job.interfaces.store import JobRecordStore
    from .core.job.use_cases.feed_pipeline import JobFeedPipeline, JobOutcome
    from .core.job.use_cases.matching import (
        JobMatchGreetingService,
        MatchGreetingResult,
        ensure_greeting_prefix,
    )
    from .core.job.use_cases.screening import (
        CandidateScreener,
        CardScreeningVerdict,
        CardVerdictStage,
        JobEvaluationResult,
        JobVerdictStage,
    )
    from .core.search.domain.models import FilterConfig, SavedSearch, SearchConfig
    from .core.shared.enums import AuthStatus, ChannelPreference
    from .core.shared.identifier_helpers import (
        format_recruiter_greeting_prefix,
        is_headhunter_agency_name,
        is_masked_company_name,
        parse_recruiter_title,
    )

_LAZY_EXPORTS: dict[str, str] = {
    "AuthStatus": ".core.shared.enums",
    "BaseBossPage": ".adapters.outbound.device.pages",
    "CandidateProfile": ".core.candidate.domain.candidate_profile",
    "CandidateScreener": ".core.job.use_cases.screening",
    "CardScreeningVerdict": ".core.job.use_cases.screening",
    "CardVerdictStage": ".core.job.use_cases.screening",
    "ChannelPreference": ".core.shared.enums",
    "ChatPage": ".adapters.outbound.device.pages",
    "FeedStreamConfig": ".core.job.domain.feed_stream",
    "FeedStreamResult": ".core.job.domain.feed_stream",
    "FilterConfig": ".core.search.domain.models",
    "FilterDialogPage": ".adapters.outbound.device.pages",
    "IndustryFilterDialogPage": ".adapters.outbound.device.pages",
    "InMemoryJobRecordStore": ".adapters.memory.job_store",
    "InMemorySavedSearchStore": ".adapters.memory.saved_search_store",
    "JobDetailPage": ".adapters.outbound.device.pages",
    "JobEvaluationResult": ".core.job.use_cases.screening",
    "JobFeedPipeline": ".core.job.use_cases.feed_pipeline",
    "JobListPage": ".adapters.outbound.device.pages",
    "JobMatchGreetingService": ".core.job.use_cases.matching",
    "JobOutcome": ".core.job.use_cases.feed_pipeline",
    "JobPosting": ".core.job.domain.job_entities",
    "JobRecordStore": ".core.job.interfaces.store",
    "JobVerdictStage": ".core.job.use_cases.screening",
    "LoginPage": ".adapters.outbound.device.pages",
    "MatchGreetingResult": ".core.job.use_cases.matching",
    "PocketBaseJobRecordStore": ".adapters.outbound.pocketbase.job_store",
    "PocketBaseSavedSearchStore": ".adapters.outbound.pocketbase.saved_search_store",
    "ProfileNormalizer": ".core.candidate.domain.profile_normalizer",
    "ResumeLifecycleState": ".adapters.outbound.resume.graph",
    "ResumeMemoryManager": ".adapters.outbound.resume.memory",
    "ResumeTextExtractor": ".adapters.outbound.resume.memory",
    "SavedSearch": ".core.search.domain.models",
    "SavedSearchStore": ".core.search.interfaces.store",
    "ScreeningPolicy": ".core.job.domain.screening_policy",
    "SearchConfig": ".core.search.domain.models",
    "SearchPage": ".adapters.outbound.device.pages",
    "SmokeHarness": ".adapters.inbound.verification.smoke_harness",
    "StartupDialogPage": ".adapters.outbound.device.pages",
    "StructuredCandidateProfile": ".core.candidate.domain.candidate_profile",
    "TakeoverHandler": ".adapters.outbound.device.takeover",
    "append_company_blacklist_entry": ".adapters.outbound.config.screening_config",
    "build_resume_lifecycle_graph": ".adapters.outbound.resume.graph",
    "ensure_greeting_prefix": ".core.job.use_cases.matching",
    "format_recruiter_greeting_prefix": ".core.shared.identifier_helpers",
    "is_headhunter_agency_name": ".core.shared.identifier_helpers",
    "is_masked_company_name": ".core.shared.identifier_helpers",
    "is_writable_screening_path": ".adapters.outbound.config.screening_config",
    "load_greeting_prompt": ".adapters.outbound.config.greeting_prompt",
    "load_settings": ".adapters.outbound.config.settings",
    "parse_recruiter_title": ".core.shared.identifier_helpers",
    "resolve_git_common_root": ".adapters.outbound.config.settings",
    "resolve_pocketbase_data_dir": ".adapters.outbound.config.settings",
    "resolve_pocketbase_db_path": ".adapters.outbound.config.settings",
    "resolve_pocketbase_url": ".adapters.outbound.config.settings",
    "resolve_saved_search_store": ".adapters.outbound.pocketbase.saved_search_store",
    "resolve_writable_screening_config_path": ".adapters.outbound.config.screening_config",
    "run_resume_lifecycle_graph": ".adapters.outbound.resume.graph",
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
    "InMemorySavedSearchStore",
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
    "PocketBaseSavedSearchStore",
    "ProfileNormalizer",
    "ResumeLifecycleState",
    "ResumeMemoryManager",
    "ResumeTextExtractor",
    "SavedSearch",
    "SavedSearchStore",
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
    "is_headhunter_agency_name",
    "is_masked_company_name",
    "load_greeting_prompt",
    "load_settings",
    "resolve_git_common_root",
    "resolve_writable_screening_config_path",
    "resolve_pocketbase_data_dir",
    "resolve_pocketbase_db_path",
    "resolve_pocketbase_url",
    "resolve_saved_search_store",
    "run_resume_lifecycle_graph",
    "ensure_greeting_prefix",
    "format_recruiter_greeting_prefix",
    "parse_recruiter_title",
]
