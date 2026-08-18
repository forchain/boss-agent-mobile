"""
boss_agent
==========
Boss 直聘 Android application automation domain layer.
"""

from .models import AuthStatus, CandidateProfile, FilterConfig, JobPosting, SearchConfig
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
    "SearchConfig",
    "SearchPage",
    "SmokeHarness",
    "StartupDialogPage",
    "TakeoverHandler",
]
