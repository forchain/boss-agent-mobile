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
    SearchConfig,
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
    "IndustryFilterDialogPage",
    "JobDetailPage",
    "JobListPage",
    "JobMatchGreetingService",
    "JobPosting",
    "LoginPage",
    "MatchGreetingResult",
    "ResumeMemoryManager",
    "ResumeTextExtractor",
    "SavedSearch",
    "SavedSearchRegistry",
    "SearchConfig",
    "SearchPage",
    "SmokeHarness",
    "StartupDialogPage",
    "StructuredCandidateProfile",
    "TakeoverHandler",
    "get_global_search_registry",
    "load_settings",
    "resolve_git_common_root",
    "resolve_pocketbase_data_dir",
    "resolve_pocketbase_db_path",
    "resolve_pocketbase_url",
]
