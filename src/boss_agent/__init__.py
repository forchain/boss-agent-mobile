"""
boss_agent
==========
Boss 直聘 Android application automation domain layer.
"""

import contextlib

with contextlib.suppress(ImportError):
    from .matching import (
        JobMatchGreetingService,
        MatchGreetingResult,
        ensure_greeting_prefix,
    )

with contextlib.suppress(ImportError):
    from .greeting_prompt import load_greeting_prompt

with contextlib.suppress(ImportError):
    from .memory import (
        ProfileNormalizer,
        ResumeMemoryManager,
        ResumeTextExtractor,
        StructuredCandidateProfile,
    )

from .candidate_entities import CandidateProfile
from .enums import AuthStatus, ChannelPreference
from .identifier_helpers import (
    format_recruiter_greeting_prefix,
    is_headhunter_agency_name,
    is_masked_company_name,
    parse_recruiter_title,
)
from .job_entities import JobPosting
from .screening_policy import ScreeningPolicy
from .search_entities import FilterConfig, SavedSearch, SearchConfig

with contextlib.suppress(ImportError):
    from .screening_config import (
        append_company_blacklist_entry,
        is_writable_screening_path,
        resolve_writable_screening_config_path,
    )
with contextlib.suppress(ImportError):
    from .screening import (
        CandidateScreener,
        CardScreeningVerdict,
        CardVerdictStage,
        JobEvaluationResult,
        JobVerdictStage,
    )

with contextlib.suppress(ImportError):
    from .job_store import (
        InMemoryJobRecordStore,
        JobRecordStore,
        PocketBaseJobRecordStore,
    )

with contextlib.suppress(ImportError):
    from .feed_pipeline import (
        FeedStreamConfig,
        FeedStreamResult,
        JobFeedPipeline,
        JobOutcome,
    )

with contextlib.suppress(ImportError):
    from .graph import (
        JobApplicationState,
        ResumeLifecycleState,
        build_job_application_graph,
        build_resume_lifecycle_graph,
        run_job_application_graph,
        run_resume_lifecycle_graph,
    )

with contextlib.suppress(ImportError):
    from .pages import (
        BaseBossPage,
        ChatPage,
        FilterDialogPage,
        IndustryFilterDialogPage,
        JobDetailPage,
        JobListPage,
        LoginPage,
        SearchPage,
        StartupDialogPage,
    )

with contextlib.suppress(ImportError):
    from .saved_search_store import (
        InMemorySavedSearchStore,
        PocketBaseSavedSearchStore,
        SavedSearchStore,
        resolve_saved_search_store,
    )

from .settings import (
    load_settings,
    resolve_git_common_root,
    resolve_pocketbase_data_dir,
    resolve_pocketbase_db_path,
    resolve_pocketbase_url,
)

with contextlib.suppress(ImportError):
    from .workflows import SmokeHarness, TakeoverHandler

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
    "JobApplicationState",
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
    "build_job_application_graph",
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
    "run_job_application_graph",
    "run_resume_lifecycle_graph",
    "ensure_greeting_prefix",
    "format_recruiter_greeting_prefix",
    "parse_recruiter_title",
]
