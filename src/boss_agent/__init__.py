"""
boss_agent
==========
Boss 直聘 Android application automation domain layer.
"""

import contextlib

with contextlib.suppress(ImportError):
    from .matching import JobMatchGreetingService, MatchGreetingResult

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
    FilterConfig,
    JobPosting,
    SavedSearch,
    ScreeningPolicy,
    SearchConfig,
    is_masked_company_name,
)

with contextlib.suppress(ImportError):
    from .graph import (
        GreetingDrafterAgent,
        JDSemanticScreenerAgent,
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
    "ChatPage",
    "FilterConfig",
    "FilterDialogPage",
    "GreetingDrafterAgent",
    "IndustryFilterDialogPage",
    "JDSemanticScreenerAgent",
    "JobApplicationState",
    "JobDetailPage",
    "JobListPage",
    "JobMatchGreetingService",
    "JobPosting",
    "LoginPage",
    "MatchGreetingResult",
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
    "build_job_application_graph",
    "build_resume_lifecycle_graph",
    "get_global_search_registry",
    "is_masked_company_name",
    "load_settings",
    "resolve_git_common_root",
    "resolve_pocketbase_data_dir",
    "resolve_pocketbase_db_path",
    "resolve_pocketbase_url",
    "run_job_application_graph",
    "run_resume_lifecycle_graph",
]
