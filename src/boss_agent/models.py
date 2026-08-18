"""
boss_agent.models
=================
Domain dataclasses for Boss 直聘 entities.
"""

from dataclasses import dataclass, field
from enum import StrEnum


class AuthStatus(StrEnum):
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


@dataclass
class SearchConfig:
    """Configuration for automated job search on Boss 直聘."""

    keyword: str | None = "agent"

    @property
    def should_search(self) -> bool:
        """Returns True if a non-empty search keyword is specified."""
        return bool(self.keyword and self.keyword.strip())


@dataclass
class FilterConfig:
    """Configuration for job filtering on Boss 直聘."""

    education: str | None = "硕士"
    salary: str | None = "5万元以上"
    experience: str | None = "10年以上"
    activity: str | None = "今日活跃"
    company_scales: list[str] = field(
        default_factory=lambda: [
            "100-499人",
            "500-999人",
            "1000-9999人",
            "10000人以上",
        ]
    )

    @property
    def has_filters(self) -> bool:
        """Returns True if any filter criteria is active."""
        return any(
            [
                bool(self.education and self.education.strip()),
                bool(self.salary and self.salary.strip()),
                bool(self.experience and self.experience.strip()),
                bool(self.activity and self.activity.strip()),
                bool(self.company_scales),
            ]
        )
