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
    enable_search: bool = True

    @property
    def should_search(self) -> bool:
        """Returns True if search is enabled and a non-empty keyword is specified."""
        return bool(self.enable_search and self.keyword and self.keyword.strip())


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
    enable_filter: bool = True

    @property
    def has_filters(self) -> bool:
        """Returns True if filtering is enabled and any filter criteria is active."""
        if not self.enable_filter:
            return False
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
        """Returns True if filtering is enabled and any industry filter criteria is active."""
        if not self.enable_filter:
            return False
        return bool(self.industries)


@dataclass
class SavedSearch:
    """Represents a named and persistent search & filter query configuration."""

    id: str
    name: str = ""
    description: str = ""
    search: SearchConfig = field(default_factory=SearchConfig)
    filter: FilterConfig = field(default_factory=FilterConfig)
    cron_expression: str = ""
    is_enabled: bool = False
    last_run_at: str | None = None
    target_task_type: str = "AUTO_APPLY"
    enable_search: bool = True
    enable_filter: bool = True

    def __post_init__(self) -> None:
        # Keep nested configs in sync with top-level flags
        if hasattr(self, "search") and self.search is not None:
            self.search.enable_search = self.enable_search
        if hasattr(self, "filter") and self.filter is not None:
            self.filter.enable_filter = self.enable_filter

    @property
    def keyword(self) -> str:
        return self.search.keyword or ""

    @keyword.setter
    def keyword(self, val: str) -> None:
        self.search.keyword = val

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "keyword": self.search.keyword,
            "enable_search": self.enable_search,
            "enable_filter": self.enable_filter,
            "search": {
                "keyword": self.search.keyword,
                "enable_search": self.enable_search,
            },
            "filter": {
                "education": self.filter.education,
                "salary": self.filter.salary,
                "experience": self.filter.experience,
                "activity": self.filter.activity,
                "company_scales": self.filter.company_scales,
                "industries": self.filter.industries,
                "enable_filter": self.enable_filter,
            },
            "cron_expression": self.cron_expression,
            "is_enabled": self.is_enabled,
            "last_run_at": self.last_run_at,
            "target_task_type": self.target_task_type,
        }

    @classmethod
    def from_dict(cls, search_id: str, data: dict[str, Any]) -> "SavedSearch":
        search_data = data.get("search", {}) or {}
        keyword = data.get("keyword")
        if keyword is None:
            keyword = search_data.get("keyword", "agent")

        filter_data = data.get("filter", {}) or {}
        if isinstance(filter_data, str):
            import json

            try:
                filter_data = json.loads(filter_data)
            except Exception:
                filter_data = {}

        enable_search = data.get("enable_search")
        if enable_search is None:
            enable_search = search_data.get("enable_search", True)
        enable_search = bool(enable_search)

        enable_filter = data.get("enable_filter")
        if enable_filter is None:
            enable_filter = filter_data.get("enable_filter", True)
        enable_filter = bool(enable_filter)

        search_cfg = SearchConfig(
            keyword=keyword,
            enable_search=enable_search,
        )
        filter_cfg = FilterConfig(
            education=filter_data.get("education"),
            salary=filter_data.get("salary"),
            experience=filter_data.get("experience"),
            activity=filter_data.get("activity"),
            company_scales=filter_data.get(
                "company_scales",
                ["100-499人", "500-999人", "1000-9999人", "10000人以上"]
                if "company_scales" not in filter_data
                else filter_data.get("company_scales", []),
            ),
            industries=filter_data.get(
                "industries",
                ["在线教育", "游戏", "人工智能"]
                if "industries" not in filter_data
                else filter_data.get("industries", []),
            ),
            enable_filter=enable_filter,
        )
        return cls(
            id=search_id,
            name=data.get("name", search_id),
            description=data.get("description", ""),
            search=search_cfg,
            filter=filter_cfg,
            cron_expression=data.get("cron_expression", "") or "",
            is_enabled=bool(data.get("is_enabled", False)),
            last_run_at=data.get("last_run_at"),
            target_task_type=data.get("target_task_type", "AUTO_APPLY"),
            enable_search=enable_search,
            enable_filter=enable_filter,
        )
