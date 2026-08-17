"""
boss_agent
==========
Boss 直聘 Android application automation domain layer.
"""

from .models import AuthStatus, CandidateProfile, JobPosting
from .pages import BaseBossPage, JobDetailPage, JobListPage, LoginPage, StartupDialogPage
from .workflows import SmokeHarness, TakeoverHandler

__all__ = [
    "AuthStatus",
    "BaseBossPage",
    "CandidateProfile",
    "JobDetailPage",
    "JobListPage",
    "JobPosting",
    "LoginPage",
    "SmokeHarness",
    "StartupDialogPage",
    "TakeoverHandler",
]
