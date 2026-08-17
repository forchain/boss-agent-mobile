"""
boss_agent
==========
Boss 直聘 Android application automation domain layer.
"""

from .models import AuthStatus, CandidateProfile, JobPosting, SearchConfig
from .pages import (
    BaseBossPage,
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
