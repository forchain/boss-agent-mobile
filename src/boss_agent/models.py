"""
boss_agent.models
=================
Domain dataclasses for Boss 直聘 entities.
"""

from dataclasses import dataclass, field
from enum import Enum


class AuthStatus(str, Enum):
    AUTHENTICATED = "AUTHENTICATED"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    CHALLENGE_REQUIRED = "CHALLENGE_REQUIRED"  # Captcha or SMS challenge


@dataclass
class JobPosting:
    title: str
    company_name: str
    salary_range: str
    job_description: str
    location: str | None = None
    tags: list[str] = field(default_factory=list)
    recruiter_name: str | None = None
    recruiter_title: str | None = None


@dataclass
class CandidateProfile:
    name: str
    target_titles: list[str]
    min_salary: int
    max_salary: int
    city: str
    resume_summary: str
    preferred_industries: list[str] = field(default_factory=list)
