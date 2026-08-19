"""
boss_agent
==========
Boss 直聘 Android application automation domain layer.
"""

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
    FilterDialogPage,
    IndustryFilterDialogPage,
    JobDetailPage,
    JobListPage,
    LoginPage,
    SearchPage,
    StartupDialogPage,
)
from .searches import SavedSearchRegistry, get_global_search_registry
from .workflows import SmokeHarness, TakeoverHandler

__all__ = [
    "AuthStatus",
    "BaseBossPage",
    "CandidateProfile",
    "FilterConfig",
    "FilterDialogPage",
    "IndustryFilterDialogPage",
    "JobDetailPage",
    "JobListPage",
    "JobPosting",
    "LoginPage",
    "SavedSearch",
    "SavedSearchRegistry",
    "SearchConfig",
    "SearchPage",
    "SmokeHarness",
    "StartupDialogPage",
    "TakeoverHandler",
    "get_global_search_registry",
]
