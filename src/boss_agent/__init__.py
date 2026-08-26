"""
boss_agent
==========
Boss 直聘 Android application automation domain layer.
"""

from .matching import JobMatchGreetingService, MatchGreetingResult
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
from .searches import SavedSearchRegistry, get_global_search_registry
from .settings import load_settings, resolve_pocketbase_url
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
    "resolve_pocketbase_url",
]

