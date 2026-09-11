"""
boss_agent.models
=================
Domain dataclasses for Boss 直聘 entities.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AuthStatus(StrEnum):
    AUTHENTICATED = "AUTHENTICATED"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    CHALLENGE_REQUIRED = "CHALLENGE_REQUIRED"  # Captcha or SMS challenge


def clean_job_title(raw_title: str) -> str:
    """Clean job title by stripping trailing status badges, tag placeholders like '&@', '@%', and excess punctuation."""
    if not raw_title:
        return ""
    if not isinstance(raw_title, str):
        raw_title = str(raw_title)
    t = raw_title.strip()
    while True:
        cleaned = re.sub(r"[\s&@%]+$", "", t).strip()
        if cleaned == t:
            break
        t = cleaned
    return t


def compute_job_fingerprint(company_name: str, title: str, recruiter_name: str) -> str:
    """Compute normalized SHA-256 fingerprint for a job card using the canonical 3 fields."""
    import hashlib

    norm_comp = (company_name or "").strip()
    norm_title = clean_job_title(title)
    norm_recruiter = (recruiter_name or "").strip()
    if any(sep in norm_recruiter for sep in ("·", "•", "・")):
        parts = [p.strip() for p in re.split(r"[·•・]", norm_recruiter, maxsplit=1)]
        norm_recruiter = parts[0].rstrip("·•・").strip()
    else:
        norm_recruiter = norm_recruiter.rstrip("·•・").strip()
    raw = f"{norm_comp}::{norm_title}::{norm_recruiter}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def extract_digest_from_jd(jd: str, max_chars: int = 100) -> str:
    """Extract a concise 1-2 sentence digest from raw job description text."""
    if not jd or not jd.strip():
        return ""
    lines = [line.strip() for line in jd.splitlines() if line.strip()]
    substantive: list[str] = []
    for line in lines:
        stripped = re.sub(r"^[0-9一二三四五六七八九十、.·•*\s\-]+", "", line).strip()
        if (
            not stripped
            or len(stripped) < 5
            or re.match(r"^(?:岗位职责|工作职责|职位描述|任职要求|任职资格|加分项|基本要求|必须要求|关于我们|公司介绍|加分条件|薪酬福利)[:：]?$", stripped)
            or re.match(r"^【(?:岗位职责|工作职责|职位描述|任职要求|任职资格|加分项|关于我们)】$", stripped)
        ):
            continue
        substantive.append(stripped)
        if len("；".join(substantive)) >= 35:
            break
    res = "；".join(substantive) if substantive else (lines[0] if lines else "")
    if len(res) > max_chars:
        res = re.sub(r"[，；、\s]+$", "", res[:max_chars]) + "..."
    return res


COMMON_TECH_TAGS = (
    "Java", "Python", "Go", "Golang", "Rust", "C++", "C#", ".NET", "PHP",
    "React Native", "React", "Flutter", "Vue", "Angular", "Node.js", "TypeScript", "JavaScript",
    "Android", "iOS", "鸿蒙", "HarmonyOS", "小程序", "RN",
    "LLM", "AI", "大模型", "Agent", "Prompt", "RAG", "AIGC", "NLP", "CV", "机器学习", "深度学习",
    "Spring", "SpringBoot", "FastAPI", "Django", "Flask",
    "MySQL", "PostgreSQL", "Redis", "MongoDB", "Elasticsearch", "Kafka",
    "Kubernetes", "K8s", "Docker", "DevOps", "CI/CD",
    "全栈", "架构师", "前端", "后端", "移动端", "测开", "运维", "微服务",
    "3-5年", "5-10年", "1-3年", "10年以上", "应届生",
    "本科", "硕士", "博士", "大专",
)


def extract_tags_from_text(text: str) -> list[str]:
    """Extract relevant skill and requirement tags from text."""
    if not text or not text.strip():
        return []
    matched: list[str] = []
    for tag in COMMON_TECH_TAGS:
        escaped = re.escape(tag)
        if re.search(rf"(?:^|[^a-zA-Z0-9_]){escaped}(?:$|[^a-zA-Z0-9_])", text, re.IGNORECASE):
            matched.append(tag)
            if len(matched) >= 5:
                break
    return matched


class JobRecordStatus(StrEnum):
    IGNORED = "ignored"
    DIGEST_ONLY = "digest_only"
    JD_SAVED = "jd_saved"
    UNMATCHED = "unmatched"
    MATCHED = "matched"
    APPLIED = "applied"


class TargetAction(StrEnum):
    DIGEST_ONLY = "digest_only"
    SAVE_JD = "save_jd"
    AUTO_APPLY = "auto_apply"


STATE_RANK: dict[str, int] = {
    JobRecordStatus.IGNORED: -1,
    JobRecordStatus.DIGEST_ONLY: 1,
    JobRecordStatus.JD_SAVED: 2,
    JobRecordStatus.UNMATCHED: 2,
    JobRecordStatus.MATCHED: 3,
    JobRecordStatus.APPLIED: 4,
}

TARGET_ACTION_RANK: dict[str, int] = {
    TargetAction.DIGEST_ONLY: 1,
    TargetAction.SAVE_JD: 2,
    TargetAction.AUTO_APPLY: 4,
}


@dataclass
class JobRecord:
    title: str
    company_name: str
    recruiter_name: str
    fingerprint: str = ""
    id: str | None = None
    salary_range: str = ""
    location: str | None = None
    digest: str = ""
    job_description: str = ""
    company_scale: str = ""
    industry: str = ""
    tags: list[str] = field(default_factory=list)
    recruiter_title: str = ""
    is_headhunter: bool = False
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
        if self.title:
            self.title = clean_job_title(self.title)
        if self.recruiter_name and any(sep in self.recruiter_name for sep in ("·", "•", "・")):
            parts = [p.strip() for p in re.split(r"[·•・]", self.recruiter_name, maxsplit=1)]
            self.recruiter_name = parts[0].rstrip("·•・").strip()
            if not self.recruiter_title and len(parts) > 1 and parts[1]:
                self.recruiter_title = parts[1].strip()
        elif self.recruiter_name:
            self.recruiter_name = self.recruiter_name.rstrip("·•・").strip()

        if not self.is_headhunter and (
            "猎头" in (self.recruiter_title or "") or "猎头" in (self.recruiter_name or "")
        ):
            self.is_headhunter = True
        if not self.fingerprint:
            self.fingerprint = compute_job_fingerprint(
                company_name=self.company_name,
                title=self.title,
                recruiter_name=self.recruiter_name,
            )
        if not self.digest and self.job_description:
            self.digest = extract_digest_from_jd(self.job_description)
        if not self.tags:
            self.tags = extract_tags_from_text(f"{self.title} {self.job_description}")


@dataclass
class JobPosting:
    title: str
    company_name: str
    salary_range: str
    job_description: str
    digest: str = ""
    location: str | None = None
    tags: list[str] = field(default_factory=list)
    recruiter_name: str | None = None
    recruiter_title: str | None = None
    company_scale: str = ""
    industry: str = ""
    is_headhunter: bool = False

    def __post_init__(self) -> None:
        if self.title:
            self.title = clean_job_title(self.title)
        if self.recruiter_name and any(sep in self.recruiter_name for sep in ("·", "•", "・")):
            parts = [p.strip() for p in re.split(r"[·•・]", self.recruiter_name, maxsplit=1)]
            self.recruiter_name = parts[0].rstrip("·•・").strip()
            if not self.recruiter_title and len(parts) > 1 and parts[1]:
                self.recruiter_title = parts[1].strip()
        elif self.recruiter_name:
            self.recruiter_name = self.recruiter_name.rstrip("·•・").strip()

        if not self.is_headhunter and (
            "猎头" in (self.recruiter_title or "") or "猎头" in (self.recruiter_name or "")
        ):
            self.is_headhunter = True
        if not self.digest and self.job_description:
            self.digest = extract_digest_from_jd(self.job_description)


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


def is_masked_company_name(name: str | None) -> bool:
    """Check whether a company name is an anonymous, confidential, or masked placeholder.

    Headhunters and agencies often use masked employer names such as:
    - '某中型人工智能公司'
    - '成都某中型...智能公司'
    - '某知名互联网公司'
    - '某大型国企'
    - '某上市公司'
    - '某独角兽'
    - '***公司' / '***'
    - '保密公司' / '保密'

    Authentic employer entities (e.g. '深至科技', '游族网络', '腾讯科技') return False.
    """
    if not name or not isinstance(name, str):
        return False

    cleaned = name.strip()
    if not cleaned:
        return False

    # Confidential / hidden placeholders
    if any(marker in cleaned for marker in ("***", "保密", "隐藏", "匿名")):
        return True

    # Check for presence of Chinese placeholder character '某' (a certain / anonymous)
    # In Chinese business naming regulations, real enterprise names never use '某'.
    # On recruitment platforms, '某' is exclusively used to mask actual company names.
    if "某" in cleaned:
        return True

    # Generic descriptors without proper names
    import re

    return bool(
        re.match(
            r"^(?:知名|头部|大型|中型|小型|外资|民营|上市|创业|初创)"
            r"(?:互联网|科技|金融|量化|医疗|AI|人工智能)?"
            r"(?:公司|企业|集团|机构|团队|厂商|大厂|外企)$",
            cleaned,
        )
    )


@dataclass
class ScreeningPolicy:
    """Policy rules for multi-stage job screening."""

    title_whitelist: list[str] = field(default_factory=list)
    title_blacklist: list[str] = field(default_factory=list)
    company_blacklist: list[str] = field(default_factory=list)
    jd_blacklist: list[str] = field(default_factory=list)
    enable_screening: bool = True

    def validate_can_blacklist_company(
        self,
        company_name: str,
        is_headhunter: bool = False,
    ) -> tuple[bool, str]:
        """Validate whether a company can safely be added to company_blacklist under guardrail rules.

        Returns (allowed: bool, notice: str).
        """
        if not company_name or not company_name.strip():
            return False, "公司名称不能为空"

        cleaned = company_name.strip()

        # Guardrail 1: Headhunter job posting's company name must not be blacklisted
        if is_headhunter:
            return (
                False,
                f"【黑名单保护生效】岗位为猎头代招岗位，公司名称 '{cleaned}' 为聚合或代招渠道，禁止加入全局黑名单以避免误伤其他雇主",
            )

        # Guardrail 2: Masked / placeholder company name must not be blacklisted
        if is_masked_company_name(cleaned):
            return (
                False,
                f"【黑名单保护生效】'{cleaned}' 属于保密/占位公司名称（如某...公司），禁止加入全局黑名单以避免大范围误伤不相关企业",
            )

        return True, f"公司 '{cleaned}' 为真实直招企业，允许加入黑名单"

    def add_company_to_blacklist(
        self,
        company_name: str,
        is_headhunter: bool = False,
    ) -> tuple[bool, str]:
        """Attempt to add a company to company_blacklist with guardrail enforcement.

        Returns (success: bool, notice: str).
        """
        allowed, notice = self.validate_can_blacklist_company(
            company_name, is_headhunter=is_headhunter
        )
        if not allowed:
            return False, notice

        cleaned = company_name.strip()
        if cleaned not in self.company_blacklist:
            self.company_blacklist.append(cleaned)
            return True, f"已成功将直招企业 '{cleaned}' 加入公司黑名单，后续该企业的岗位将自动过滤以节省每日投递额度"
        return True, f"企业 '{cleaned}' 已在公司黑名单中"

    def remove_company_from_blacklist(self, company_name: str) -> bool:
        """Remove a company from company_blacklist."""
        cleaned = company_name.strip()
        if cleaned in self.company_blacklist:
            self.company_blacklist.remove(cleaned)
            return True
        return False

    def matches_card_keywords(
        self,
        title: str,
        company_name: str = "",
        tags: list[str] | None = None,
        digest: str = "",
    ) -> tuple[bool, str]:
        """Deterministic keyword evaluation for job card.

        Evaluates title, company_name, tags, and digest against screening policy.
        Returns (passed: bool, reason: str).
        """
        if not self.enable_screening:
            return True, "筛选策略未启用，默认通过"

        norm_title = (title or "").lower()
        norm_company = (company_name or "").lower()
        norm_tags = [t.lower() for t in (tags or [])]
        norm_digest = (digest or "").lower()

        # 1. Check title blacklist (一票否决: 检查 title 和 tags)
        for black in self.title_blacklist:
            b = black.strip().lower()
            if b and (b in norm_title or any(b in t for t in norm_tags)):
                return False, f"命中职位黑名单关键词: '{black}'"

        # 2. Check company blacklist (一票否决: 检查 company_name)
        for black in self.company_blacklist:
            b = black.strip().lower()
            if b and b in norm_company:
                return False, f"命中公司黑名单关键词: '{black}'"

        # 3. Check JD/Digest blacklist (一票否决: 检查 digest 和 tags)
        for black in self.jd_blacklist:
            b = black.strip().lower()
            if b and (b in norm_digest or any(b in t for t in norm_tags)):
                return False, f"命中岗位摘要/标签黑名单关键词: '{black}'"

        # 4. Check title whitelist (若配置了白名单，必须在 title, tags, 或 digest 中命中至少一个)
        active_whitelist = [w.strip().lower() for w in self.title_whitelist if w.strip()]
        if active_whitelist:
            hit = any(
                w in norm_title or any(w in t for t in norm_tags) or w in norm_digest
                for w in active_whitelist
            )
            if not hit:
                return False, f"未命中任何职位白名单关键词 (要求: {self.title_whitelist})"

        return True, "通过卡片初筛"

    def to_dict(self) -> dict[str, Any]:
        return {
            "title_whitelist": self.title_whitelist,
            "title_blacklist": self.title_blacklist,
            "company_blacklist": self.company_blacklist,
            "jd_blacklist": self.jd_blacklist,
            "enable_screening": self.enable_screening,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ScreeningPolicy":
        if not data or not isinstance(data, dict):
            return cls()
        return cls(
            title_whitelist=list(data.get("title_whitelist") or []),
            title_blacklist=list(data.get("title_blacklist") or []),
            company_blacklist=list(data.get("company_blacklist") or []),
            jd_blacklist=list(data.get("jd_blacklist") or []),
            enable_screening=bool(data.get("enable_screening", True)),
        )


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
    target_action: str = "save_jd"
    max_jobs: int = 20
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
            "target_action": self.target_action,
            "max_jobs": self.max_jobs,
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
            "screening_policy": self.screening_policy.to_dict(),
            "cron_expression": self.cron_expression,
            "is_enabled": self.is_enabled,
            "last_run_at": self.last_run_at,
            "target_task_type": self.target_task_type,
        }

    @classmethod
    def from_dict(cls, search_id: str | dict[str, Any], data: dict[str, Any] | None = None) -> "SavedSearch":
        if isinstance(search_id, dict) and data is None:
            data = search_id
            search_id = str(data.get("id", ""))

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

        policy_data = data.get("screening_policy", {}) or {}
        if isinstance(policy_data, str):
            import json

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

        return cls(
            id=search_id,
            name=data.get("name", search_id),
            description=data.get("description", ""),
            search=search_cfg,
            filter=filter_cfg,
            screening_policy=screening_policy,
            cron_expression=data.get("cron_expression", "") or "",
            is_enabled=bool(data.get("is_enabled", False)),
            last_run_at=data.get("last_run_at"),
            target_task_type=data.get("target_task_type", "AUTO_APPLY"),
            target_action=data.get("target_action", "save_jd"),
            max_jobs=int(data.get("max_jobs", 20)),
            enable_search=enable_search,
            enable_filter=enable_filter,
        )
