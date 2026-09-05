"""
boss_agent.models
=================
Domain dataclasses for Boss 直聘 entities.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AuthStatus(StrEnum):
    AUTHENTICATED = "AUTHENTICATED"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    CHALLENGE_REQUIRED = "CHALLENGE_REQUIRED"  # Captcha or SMS challenge


def compute_job_fingerprint(company_name: str, title: str, recruiter_name: str) -> str:
    """Compute normalized SHA-256 fingerprint for a job card using the canonical 3 fields."""
    import hashlib

    norm_comp = (company_name or "").strip()
    norm_title = (title or "").strip()
    norm_recruiter = (recruiter_name or "").strip()
    raw = f"{norm_comp}::{norm_title}::{norm_recruiter}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class JobRecordStatus(StrEnum):
    UNMATCHED = "unmatched"
    MATCHED = "matched"
    APPLIED = "applied"
    IGNORED = "ignored"


@dataclass
class JobRecord:
    title: str
    company_name: str
    recruiter_name: str
    fingerprint: str = ""
    id: str | None = None
    salary_range: str = ""
    location: str | None = None
    job_description: str = ""
    status: str = "unmatched"
    match_score: int | None = None
    jd_key_requirements: list[str] = field(default_factory=list)
    greeting_message: str = ""
    search_keywords: list[str] = field(default_factory=list)
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    source_task_id: str | None = None
    created: str | None = None
    updated: str | None = None

    def __post_init__(self) -> None:
        if not self.fingerprint:
            self.fingerprint = compute_job_fingerprint(
                company_name=self.company_name,
                title=self.title,
                recruiter_name=self.recruiter_name,
            )


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
    industries: list[str] = field(default_factory=list)

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
                bool(self.industries),
            ]
        )

    @property
    def has_industry_filters(self) -> bool:
        """Returns True if any industry filter criteria is active."""
        return bool(self.industries)


@dataclass
class SavedSearch:
    """Represents a named and persistent search & filter query configuration."""

    id: str
    name: str = ""
    description: str = ""
    search: SearchConfig = field(default_factory=SearchConfig)
    filter: FilterConfig = field(default_factory=FilterConfig)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "search": {
                "keyword": self.search.keyword,
            },
            "filter": {
                "education": self.filter.education,
                "salary": self.filter.salary,
                "experience": self.filter.experience,
                "activity": self.filter.activity,
                "company_scales": self.filter.company_scales,
                "industries": self.filter.industries,
            },
        }

    @classmethod
    def from_dict(cls, search_id: str, data: dict[str, Any]) -> "SavedSearch":
        search_data = data.get("search", {}) or {}
        filter_data = data.get("filter", {}) or {}

        search_cfg = SearchConfig(
            keyword=search_data.get("keyword", "agent"),
        )
        filter_cfg = FilterConfig(
            education=filter_data.get("education", "硕士"),
            salary=filter_data.get("salary", "5万元以上"),
            experience=filter_data.get("experience", "10年以上"),
            activity=filter_data.get("activity", "今日活跃"),
            company_scales=filter_data.get(
                "company_scales",
                ["100-499人", "500-999人", "1000-9999人", "10000人以上"],
            ),
            industries=filter_data.get("industries", ["在线教育", "游戏", "人工智能"]),
        )
        return cls(
            id=search_id,
            name=data.get("name", search_id),
            description=data.get("description", ""),
            search=search_cfg,
            filter=filter_cfg,
        )
