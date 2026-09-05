"""
boss_agent
==========
Boss 直聘 Android application automation domain layer.
"""

try:
    from .matching import JobMatchGreetingService, MatchGreetingResult
except ImportError:
    pass

try:
    from .memory import (
        ResumeMemoryManager,
        ResumeTextExtractor,
        StructuredCandidateProfile,
    )
except ImportError:
    pass

from .models import (
    AuthStatus,
    CandidateProfile,
    FilterConfig,
    JobPosting,
    SavedSearch,
    SearchConfig,
)

try:
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
except ImportError:
    pass

try:
    from .searches import SavedSearchRegistry, get_global_search_registry
except ImportError:
    pass
from .settings import (
    load_settings,
    resolve_git_common_root,
    resolve_pocketbase_data_dir,
    resolve_pocketbase_db_path,
    resolve_pocketbase_url,
)

try:
    from .workflows import SmokeHarness, TakeoverHandler
except ImportError:
    pass

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
    "resolve_pocketbase_url",
]

