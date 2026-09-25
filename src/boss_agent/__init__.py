"""
boss_agent
==========
Boss 直聘 Android application automation domain layer.
"""

import contextlib

with contextlib.suppress(ImportError):
    from .matching import JobMatchGreetingService, MatchGreetingResult

with contextlib.suppress(ImportError):
    from .greeting_prompt import load_greeting_prompt

with contextlib.suppress(ImportError):
    from .memory import (
        ProfileNormalizer,
        ResumeMemoryManager,
        ResumeTextExtractor,
        StructuredCandidateProfile,
    )

from .models import (
    AuthStatus,
    CandidateProfile,
    ChannelPreference,
    FilterConfig,
    JobPosting,
    SavedSearch,
    ScreeningPolicy,
    SearchConfig,
    append_company_blacklist_entry,
    is_headhunter_agency_name,
    is_masked_company_name,
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
    from .searches import SavedSearchRegistry, get_global_search_registry
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
    "build_job_application_graph",
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
    "run_job_application_graph",
    "run_resume_lifecycle_graph",
]
