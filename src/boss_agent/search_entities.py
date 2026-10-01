"""
boss_agent.search_entities
==========================
Search configuration, filter presets, and SavedSearch domain entities (Issue #311, Spec #303).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from boss_agent.enums import CHECK_CHAT_ACTION, TargetAction, TargetTaskType
from boss_agent.screening_policy import ScreeningPolicy


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
                bool(
                    self.education and self.education.strip() and self.education.strip() != "不限"
                ),
                bool(self.salary and self.salary.strip() and self.salary.strip() != "不限"),
                bool(
                    self.experience
                    and self.experience.strip()
                    and self.experience.strip() != "不限"
                ),
                bool(self.activity and self.activity.strip() and self.activity.strip() != "不限"),
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


def _saved_search_max_jobs_default() -> int:
    """The declared ``saved_searches.max_jobs`` default, resolved lazily.

    The search entities module is imported by the broker adapter, so importing the
    Collection Schema at module scope here would close an import cycle. The default
    itself lives in the schema module so the domain model, the provisioner and the
    Web UI cannot drift apart the way 20-vs-30 once did.
    """
    from boss_agent.broker.collection_schema import SAVED_SEARCH_MAX_JOBS

    return SAVED_SEARCH_MAX_JOBS


@dataclass
class SavedSearch:
    """Represents a named and persistent search & filter query configuration."""

    id: str
    name: str = ""
    description: str = ""
    search: SearchConfig = field(default_factory=SearchConfig)
    filter: FilterConfig = field(default_factory=FilterConfig)
    screening_policy: ScreeningPolicy = field(default_factory=ScreeningPolicy)
    cron_expression: str = ""
    is_enabled: bool = False
    last_run_at: str | None = None
    target_task_type: str = "AUTO_APPLY"
    target_action: str = ""
    max_jobs: int = field(default_factory=_saved_search_max_jobs_default)

    def __init__(
        self,
        id: str,
        name: str = "",
        description: str = "",
        search: SearchConfig | None = None,
        filter: FilterConfig | None = None,
        screening_policy: ScreeningPolicy | None = None,
        cron_expression: str = "",
        is_enabled: bool = False,
        last_run_at: str | None = None,
        target_task_type: str = "AUTO_APPLY",
        target_action: str = "",
        max_jobs: int | None = None,
        enable_search: bool | None = None,
        enable_filter: bool | None = None,
    ) -> None:
        self.id = id
        self.name = name
        self.description = description
        self.search = search if search is not None else SearchConfig()
        self.filter = filter if filter is not None else FilterConfig()
        self.screening_policy = (
            screening_policy if screening_policy is not None else ScreeningPolicy()
        )
        self.cron_expression = cron_expression
        self.is_enabled = is_enabled
        self.last_run_at = last_run_at
        self.target_task_type = target_task_type
        self.target_action = target_action
        self.max_jobs = max_jobs if max_jobs is not None else _saved_search_max_jobs_default()
        if enable_search is not None:
            self.search.enable_search = bool(enable_search)
        if enable_filter is not None:
            self.filter.enable_filter = bool(enable_filter)

        # Bidirectional sync between target_action and target_task_type.
        # CHECK_CHAT (inbox rejection cleanup) is keyword-independent, so it is
        # resolved first and never falls through to a search target.
        if self.is_chat_cleanup:
            self.target_task_type = TargetTaskType.CHECK_CHAT
            self.target_action = CHECK_CHAT_ACTION
        elif not self.target_action or self.target_action == "digest_only":
            self.target_action = (
                TargetAction.AUTO_APPLY
                if self.target_task_type == TargetTaskType.AUTO_APPLY
                else TargetAction.SAVE_JD
            )
        elif self.target_action == TargetAction.AUTO_APPLY:
            self.target_task_type = TargetTaskType.AUTO_APPLY
        else:
            self.target_task_type = TargetTaskType.SCRAPE_JOBS

    @property
    def enable_search(self) -> bool:
        return self.search.enable_search

    @enable_search.setter
    def enable_search(self, val: bool) -> None:
        self.search.enable_search = bool(val)

    @property
    def enable_filter(self) -> bool:
        return self.filter.enable_filter

    @enable_filter.setter
    def enable_filter(self, val: bool) -> None:
        self.filter.enable_filter = bool(val)

    @property
    def is_chat_cleanup(self) -> bool:
        """True when this strategy runs New Greeting Inbox cleanup, not a search."""
        return (
            self.target_task_type == TargetTaskType.CHECK_CHAT
            or self.target_action == CHECK_CHAT_ACTION
        )

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
            "target_action": self.target_action,
            "max_jobs": self.max_jobs,
            "search": {
                "keyword": self.search.keyword,
                "enable_search": self.search.enable_search,
            },
            "filter": {
                "education": self.filter.education,
                "salary": self.filter.salary,
                "experience": self.filter.experience,
                "activity": self.filter.activity,
                "company_scales": self.filter.company_scales,
                "industries": self.filter.industries,
                "enable_filter": self.filter.enable_filter,
            },
            "screening_policy": self.screening_policy.to_dict(),
            "cron_expression": self.cron_expression,
            "is_enabled": self.is_enabled,
            "last_run_at": self.last_run_at,
            "target_task_type": self.target_task_type,
        }

    @classmethod
    def from_dict(
        cls, search_id: str | dict[str, Any], data: dict[str, Any] | None = None
    ) -> SavedSearch:
        if isinstance(search_id, dict) and data is None:
            data = search_id
            search_id = str(data.get("id", ""))

        data = data or {}
        sid = str(search_id)
        search_data = data.get("search", {}) or {}
        keyword = data.get("keyword")
        if keyword is None:
            keyword = search_data.get("keyword", "agent")

        filter_data = data.get("filter", {}) or {}
        if isinstance(filter_data, str):
            try:
                filter_data = json.loads(filter_data)
            except Exception:
                filter_data = {}

        # The nested spelling is authoritative; fall back to legacy top-level only when nested is absent.
        if "enable_search" in search_data:
            enable_search = bool(search_data["enable_search"])
        elif "enable_search" in data:
            enable_search = bool(data["enable_search"])
        else:
            enable_search = True

        if "enable_filter" in filter_data:
            enable_filter = bool(filter_data["enable_filter"])
        elif "enable_filter" in data:
            enable_filter = bool(data["enable_filter"])
        else:
            enable_filter = True

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

        policy_data = data.get("screening_policy", {}) or {}
        if isinstance(policy_data, str):
            try:
                policy_data = json.loads(policy_data)
            except Exception:
                policy_data = {}

        # Allow fallback from top-level keys if screening_policy not nested
        if not policy_data and any(
            k in data
            for k in ("title_whitelist", "title_blacklist", "company_blacklist", "jd_blacklist")
        ):
            policy_data = {
                "title_whitelist": data.get("title_whitelist"),
                "title_blacklist": data.get("title_blacklist"),
                "company_blacklist": data.get("company_blacklist"),
                "jd_blacklist": data.get("jd_blacklist"),
            }

        screening_policy = ScreeningPolicy.from_dict(policy_data)

        target_task_type = data.get("target_task_type", TargetTaskType.AUTO_APPLY)
        target_action = data.get("target_action")
        if not target_action:
            if target_task_type == TargetTaskType.CHECK_CHAT:
                target_action = CHECK_CHAT_ACTION
            else:
                target_action = (
                    TargetAction.AUTO_APPLY
                    if target_task_type == TargetTaskType.AUTO_APPLY
                    else TargetAction.SAVE_JD
                )

        raw_max_jobs = data.get("max_jobs")
        max_jobs = (
            int(raw_max_jobs) if raw_max_jobs is not None else _saved_search_max_jobs_default()
        )

        return cls(
            id=sid,
            name=data.get("name", sid),
            description=data.get("description", ""),
            search=search_cfg,
            filter=filter_cfg,
            screening_policy=screening_policy,
            cron_expression=data.get("cron_expression", "") or "",
            is_enabled=bool(data.get("is_enabled", False)),
            last_run_at=data.get("last_run_at"),
            target_task_type=target_task_type,
            target_action=target_action,
            max_jobs=max_jobs,
            enable_search=enable_search,
            enable_filter=enable_filter,
        )
