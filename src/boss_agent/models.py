"""
boss_agent.models
=================
Domain dataclasses for Boss 直聘 entities.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
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


#: Characters Boss uses between a recruiter's name and the title it appends.
RECRUITER_SEPARATORS: tuple[str, ...] = ("·", "•", "・")
_RECRUITER_SEPARATOR_RE = re.compile(r"[·•・]")

#: Platform chrome that appears as its own text node or tag on a job card. Declared once
#: so the card parser, the locator read and the tag sanitizer cannot disagree about
#: which strings are badges rather than content.
PLATFORM_BADGE_MARKERS: frozenset[str] = frozenset({"猎", "新", "急", "热", "置顶"})


def split_recruiter_name(raw: str) -> tuple[str, str]:
    """``(name, title)`` for a recruiter string, split on the first name/title separator.

    Boss renders the recruiter as "钟先生 · 猎头顾问" on some card layouts and
    "钟先生·猎头顾问" on others, and the name is always the part before the first
    separator. This is the one place that rule is expressed: the Job Fingerprint, the
    card brief, ``JobRecord``, ``JobPosting`` and the legacy-record backfill all
    normalize through it, so they cannot disagree about who the recruiter was.
    """
    text = (raw or "").strip()
    if not text:
        return "", ""
    parts = _RECRUITER_SEPARATOR_RE.split(text, maxsplit=1)
    name = parts[0].rstrip("·•・").strip()
    title = parts[1].strip() if len(parts) > 1 else ""
    return name, title


def normalize_recruiter_name(raw: str) -> str:
    """The recruiter's name alone, with any title Boss appended after a separator removed."""
    return split_recruiter_name(raw)[0]


def compute_job_fingerprint(company_name: str, title: str, recruiter_name: str) -> str:
    """Compute normalized SHA-256 fingerprint for a job card using the canonical 3 fields."""
    import hashlib

    norm_comp = (company_name or "").strip()
    norm_title = clean_job_title(title)
    norm_recruiter = normalize_recruiter_name(recruiter_name)
    raw = f"{norm_comp}::{norm_title}::{norm_recruiter}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


KNOWN_CITIES: tuple[str, ...] = (
    "上海",
    "北京",
    "深圳",
    "广州",
    "杭州",
    "成都",
    "武汉",
    "南京",
    "苏州",
    "西安",
    "重庆",
    "天津",
    "长沙",
    "厦门",
    "合肥",
    "青岛",
    "郑州",
    "大连",
    "海外",
    "远程",
)

RECRUITER_TITLE_KEYWORDS: tuple[str, ...] = (
    "猎头",
    "顾问",
    "专员",
    "专家",
    "HR",
    "招聘",
    "经理",
    "主管",
    "总监",
    "助理",
    "VP",
    "合伙人",
    "Recruiter",
    "Leader",
    "HRBP",
    "负责人",
    "人事",
    "招聘者",
)

EDUCATION_KEYWORDS: frozenset[str] = frozenset(
    {
        "本科",
        "硕士",
        "大专",
        "博士",
        "学历不限",
        "初中及以下",
        "中专/中技",
        "高中",
        "大专及以上",
        "本科及以上",
        "硕士及以上",
        "MBA/EMBA",
    }
)

EXPERIENCE_KEYWORDS: frozenset[str] = frozenset(
    {
        "经验不限",
        "应届生",
        "在校生",
        "应届毕业生",
    }
)


COMPANY_INDICATOR_KEYWORDS: tuple[str, ...] = (
    "公司",
    "科技",
    "网络",
    "集团",
    "企业",
    "银行",
    "证券",
    "基金",
    "保险",
    "有限",
    "工作室",
    "事务所",
    "中心",
    "信息",
    "数据",
    "通信",
    "智能",
    "工业",
    "制造",
    "电商",
    "商贸",
    "游戏",
    "互娱",
    "软件",
    "技术",
    "医疗",
    "健康",
    "生物",
    "制药",
    "教育",
    "文化",
    "传媒",
)


def is_likely_location(s: str) -> bool:
    """Check if a string is likely a geographic location rather than a recruiter title or company info."""
    if not s or not isinstance(s, str):
        return False
    t = s.strip()
    if any(kw in t for kw in RECRUITER_TITLE_KEYWORDS):
        return False
    if any(kw in t for kw in COMPANY_INDICATOR_KEYWORDS) or "某" in t:
        return False
    if t in KNOWN_CITIES:
        return True
    if len(t) <= 8 and (
        t.endswith("市")
        or t.endswith("区")
        or t.endswith("县")
        or t.endswith("省")
        or t.endswith("镇")
        or t.endswith("道")
    ):
        return True
    sub_parts = t.split()
    return bool(len(sub_parts) >= 2 and any(p in KNOWN_CITIES for p in sub_parts))


INVALID_COMPANY_NAMES: frozenset[str] = frozenset(
    {"", "未知公司", "未注明公司", "null", "undefined"}
)


def is_invalid_company_name(name: str) -> bool:
    """Check if a candidate string is an invalid company name (e.g. education, experience, recruiter, location, scale, job duty)."""
    if not name or not isinstance(name, str):
        return True
    c = name.strip()
    if not c or c in INVALID_COMPANY_NAMES:
        return True
    if len(c) > 40:
        return True
    if (
        c.startswith("负责")
        or c.startswith("岗位")
        or c.startswith("任职")
        or c.startswith("工作职责")
        or c.startswith("职位描述")
    ):
        return True
    if c in EDUCATION_KEYWORDS or c.endswith("学历"):
        return True
    if c in EXPERIENCE_KEYWORDS or bool(re.search(r"^\d+[-~至]?\d*年", c)):
        return True
    if any(sep in c for sep in ("·", "•", "・")):
        return True
    if any(kw in c for kw in ("猎头", "顾问", "招聘", "HR", "人事")) and len(c) <= 15:
        return True
    if bool(re.search(r"^(\d+[-~至]\d+人|\d+人以上|少于\d+人|\d+人以下|\d+人)$", c)):
        return True
    if any(kw in c for kw in COMPANY_INDICATOR_KEYWORDS) or "某" in c:
        return False
    return bool(c in KNOWN_CITIES or is_likely_location(c))


def sanitize_tags(
    tags: list[str] | None,
    recruiter_name: str = "",
    recruiter_title: str = "",
    location: str = "",
    company_name: str = "",
    title: str = "",
) -> list[str]:
    """Sanitize and deduplicate job tags, strictly stripping recruiter info, location, scale, and company."""
    if not tags:
        return []
    r_name = (recruiter_name or "").strip()
    r_title = (recruiter_title or "").strip()
    loc = (location or "").strip()
    comp = (company_name or "").strip()
    tit = (title or "").strip()

    cleaned: list[str] = []
    for raw in tags:
        if not raw:
            continue
        t = str(raw).strip()
        if not t or t in PLATFORM_BADGE_MARKERS or len(t) > 25:
            continue
        # Recruiter filtering
        if any(sep in t for sep in RECRUITER_SEPARATORS):
            continue
        if any(kw in t for kw in ("猎头", "顾问", "HR", "人事", "招聘专员", "招聘者", "Recruiter")):
            continue
        if r_name and len(r_name) >= 2 and (t == r_name or r_name in t):
            continue
        if r_title and len(r_title) >= 2 and (t == r_title or r_title in t):
            continue
        # Location filtering
        if loc and (t == loc or loc in t or t in loc):
            continue
        if is_likely_location(t) or t in KNOWN_CITIES:
            continue
        if t.endswith("市") or t.endswith("区") or t.endswith("县"):
            continue
        # Scale / Company / Title filtering
        if re.search(r"(\d+[-~至]\d+人|\d+人以上|少于\d+人|\d+人以下|\d+人)", t):
            continue
        if comp and len(comp) >= 2 and t == comp:
            continue
        if tit and len(tit) >= 2 and t == tit:
            continue
        if t.startswith("负责"):
            continue
        if t not in cleaned:
            cleaned.append(t)
    return cleaned


_CN_JD_HEADER_WORDS = (
    "岗位职责|工作职责|职位描述|任职要求|任职资格|加分项|基本要求|必须要求|关于我们|公司介绍|加分条件|薪酬福利"
)
_EN_JD_HEADER_WORDS = (
    r"role summary|role overview|job summary|job description|responsibilit(?:y|ies)|"
    r"requirement(?:s)?|qualification(?:s)?|preferred (?:qualifications|requirements)|"
    r"skills|about (?:us|the (?:company|team|role|job))|benefits|compensation|perks"
)
_JD_HEADER_ANNOTATION = r"(?:\s*[【\[(（][^】\]）]*[】\]）])?"
_JD_HEADER_STANDALONE_RE = re.compile(
    rf"^\s*[【\[(（]?\s*(?:{_CN_JD_HEADER_WORDS}|{_EN_JD_HEADER_WORDS})\s*[】\]）]?"
    rf"{_JD_HEADER_ANNOTATION}\s*[:：]?\s*[；;，,。.]?\s*$",
    re.IGNORECASE,
)
_JD_HEADER_PREFIX_RE = re.compile(
    rf"^[【\[(（]\s*(?:{_CN_JD_HEADER_WORDS}|{_EN_JD_HEADER_WORDS})\s*[】\]）]{_JD_HEADER_ANNOTATION}?"
    rf"\s*[:：]?\s*[；;，,。.]?\s*",
    re.IGNORECASE,
)


def extract_digest_from_jd(jd: str, max_chars: int = 100) -> str:
    """Extract a concise 1-2 sentence digest from raw job description text."""
    if not jd or not jd.strip():
        return ""
    lines = [line.strip() for line in jd.splitlines() if line.strip()]
    substantive: list[str] = []
    for line in lines:
        stripped = re.sub(r"^[0-9一二三四五六七八九十、.·•*\s\-]+", "", line).strip()
        stripped = _JD_HEADER_PREFIX_RE.sub("", stripped, count=1).strip()
        if not stripped or len(stripped) < 5 or _JD_HEADER_STANDALONE_RE.match(stripped):
            continue
        substantive.append(stripped)
        if len("；".join(substantive)) >= 35:
            break
    res = "；".join(substantive) if substantive else (lines[0] if lines else "")
    if len(res) > max_chars:
        cut = res[:max_chars]
        # Never end on a half-cut Latin word: fall back to the last full word boundary.
        if cut[-1:].isascii() and cut[-1:].isalpha():
            boundary = cut.rfind(" ")
            if boundary > max_chars // 2:
                cut = cut[:boundary]
        res = re.sub(r"[，；、\s.]+$", "", cut) + "..."
    return res


COMMON_TECH_TAGS = (
    "Java",
    "Python",
    "Go",
    "Golang",
    "Rust",
    "C++",
    "C#",
    ".NET",
    "PHP",
    "React Native",
    "React",
    "Flutter",
    "Vue",
    "Angular",
    "Node.js",
    "TypeScript",
    "JavaScript",
    "Android",
    "iOS",
    "鸿蒙",
    "HarmonyOS",
    "小程序",
    "RN",
    "LLM",
    "AI",
    "大模型",
    "Agent",
    "Prompt",
    "RAG",
    "AIGC",
    "NLP",
    "CV",
    "机器学习",
    "深度学习",
    "Spring",
    "SpringBoot",
    "FastAPI",
    "Django",
    "Flask",
    "MySQL",
    "PostgreSQL",
    "Redis",
    "MongoDB",
    "Elasticsearch",
    "Kafka",
    "Kubernetes",
    "K8s",
    "Docker",
    "DevOps",
    "CI/CD",
    "全栈",
    "架构师",
    "前端",
    "后端",
    "移动端",
    "测开",
    "运维",
    "微服务",
    "3-5年",
    "5-10年",
    "1-3年",
    "10年以上",
    "应届生",
    "本科",
    "硕士",
    "博士",
    "大专",
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
    JD_SAVED = "jd_saved"
    UNMATCHED = "unmatched"
    MATCHED = "matched"
    APPLIED = "applied"
    # Backward compatibility for legacy database records
    DIGEST_ONLY = "digest_only"


class TargetAction(StrEnum):
    SAVE_JD = "save_jd"
    AUTO_APPLY = "auto_apply"


#: SavedSearch target_action value for inbox cleanup strategies.
CHECK_CHAT_ACTION: str = "check_chat"


class TargetTaskType(StrEnum):
    """Worker task a strategy dispatches. `CHECK_CHAT` is deliberately outside
    :class:`TargetAction`: it is keyword-independent inbox cleanup, not a search
    depth, so it never takes part in the target-action rank ladder."""

    SCRAPE_JOBS = "SCRAPE_JOBS"
    AUTO_APPLY = "AUTO_APPLY"
    CHECK_CHAT = "CHECK_CHAT"


class ChatButtonState(StrEnum):
    """Engagement state reported by the detail page call-to-action button (`btn_chat`)."""

    UNCONTACTED = "uncontacted"
    COMMUNICATED = "communicated"
    CLOSED = "closed"
    UNKNOWN = "unknown"


COMMUNICATED_BUTTON_TEXTS: tuple[str, ...] = ("继续沟通",)
CLOSED_BUTTON_TEXTS: tuple[str, ...] = ("停止招聘", "职位已关闭", "已下线")
UNCONTACTED_BUTTON_TEXTS: tuple[str, ...] = ("立即沟通", "聊一聊", "去沟通", "发消息")


def classify_chat_button(button_text: str | None, enabled: bool = True) -> ChatButtonState:
    """Classify the `btn_chat` call-to-action text into an engagement state.

    Fail-open by design: unrecognised, missing, or disabled controls yield UNKNOWN so that the
    caller keeps its normal evaluation flow rather than silently burying a live posting.
    """
    text = (button_text or "").strip()
    if not text:
        return ChatButtonState.UNKNOWN
    if any(marker in text for marker in COMMUNICATED_BUTTON_TEXTS):
        return ChatButtonState.COMMUNICATED
    if any(marker in text for marker in CLOSED_BUTTON_TEXTS):
        return ChatButtonState.CLOSED
    if any(marker in text for marker in UNCONTACTED_BUTTON_TEXTS):
        return ChatButtonState.UNCONTACTED if enabled else ChatButtonState.UNKNOWN
    return ChatButtonState.UNKNOWN


# Provenance of an `applied` record: agent-dispatched greeting vs. pre-existing platform contact.
APPLIED_SOURCE_AGENT = "agent_auto_send"
APPLIED_SOURCE_PLATFORM_HISTORICAL = "platform_historical"

EXPIRED_POSTING_REASON = "岗位已失效/停止招聘"

# Telemetry shared by both detail-inspecting handlers when a headhunter posting is
# spared the bottom commute probe (ticket #255). Kept in one place so the two paths
# cannot drift into reporting the skip differently.
HEADHUNTER_COMMUTE_PROBE_SKIP_REASON = "猎头岗位（企业信息保密），跳过底部通勤距离探测以节省耗时"

DEFAULT_COMMUNICATION_COOLDOWN_DAYS = 30


# --------------------------------------------------------------------------- #
# Job card observation
# --------------------------------------------------------------------------- #
#
# Read off a search/list card before any detail page is opened. It is pure domain
# data: it used to carry an opaque Appium element handle, which forced the Candidate
# Screener and the feed-record builders to import the page-object module just to read
# text. The handle now lives on `pages.LocatedJobCard`, which the feed pipeline alone
# consumes for viewport geometry and the detail tap.


@dataclass
class JobCardBrief:
    """Lightweight extraction from a job card in search/list view for deduplication."""

    title: str
    company_name: str
    recruiter_name: str
    fingerprint: str = ""
    salary_range: str = ""
    location: str = ""
    tags: list[str] = field(default_factory=list)
    digest: str = ""
    snippet: str = ""
    company_scale: str = ""
    industry: str = ""
    recruiter_title: str = ""
    is_headhunter: bool = False
    # App-Enforced commute distance carried over from the detail page probe
    # (spec #209). None = unknown; screening fails open on it.
    commute_distance_km: float | None = None
    commute_distance_text: str = ""

    def __post_init__(self) -> None:
        if self.title:
            self.title = clean_job_title(self.title)
        if self.recruiter_name:
            self.recruiter_name, derived_recruiter_title = split_recruiter_name(self.recruiter_name)
            if not self.recruiter_title and derived_recruiter_title:
                self.recruiter_title = derived_recruiter_title

        if not self.digest and self.snippet:
            self.digest = self.snippet
        elif self.digest and not self.snippet:
            self.snippet = self.digest

        self.is_headhunter = resolve_headhunter_channel(
            self.is_headhunter, self.recruiter_name, self.recruiter_title
        )
        self.tags = sanitize_tags(
            self.tags,
            recruiter_name=self.recruiter_name,
            recruiter_title=self.recruiter_title,
            location=self.location,
            company_name=self.company_name,
            title=self.title,
        )
        if not self.fingerprint:
            self.fingerprint = compute_job_fingerprint(
                company_name=self.company_name,
                title=self.title,
                recruiter_name=self.recruiter_name,
            )


def resolve_headhunter_channel(
    is_headhunter: bool | None,
    recruiter_name: str | None = "",
    recruiter_title: str | None = "",
) -> bool:
    """Resolve the recruitment channel, inferring it from the recruiter when unset.

    A posting whose channel is unknown but whose recruiter says 猎头 is a headhunter
    posting: leaving it as a direct hire would let it slip past a direct-only channel
    preference. One rule, so cards, postings and screening facets cannot drift apart.
    """
    if is_headhunter:
        return True
    return "猎头" in (recruiter_title or "") or "猎头" in (recruiter_name or "")


def is_direct_hire_company(company_name: str | None, is_headhunter: bool | None) -> bool:
    """Whether a posting belongs to a genuine direct-hire enterprise for同企避嫌 purposes.

    Headhunter channels represent disparate clients and masked/confidential employer names
    cannot be reliably attributed, so neither ever participates in company-wide exclusion.
    """
    if is_headhunter:
        return False
    name = (company_name or "").strip()
    return bool(name) and not is_masked_company_name(name)


def parse_utc_timestamp(raw: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp (with or without trailing 'Z') into an aware UTC datetime."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def is_communication_expired(
    record: dict[str, Any],
    cooldown_days: int,
    now: datetime | None = None,
) -> bool:
    """Whether a past communication has aged out of the re-application cool-down window.

    `cooldown_days <= 0` means permanent suppression: nothing ever expires automatically.
    Platform historical contacts carry no `applied_at`, so their ingestion date (`created`)
    governs expiry instead. Records without any usable timestamp never expire by accident.
    """
    if cooldown_days <= 0:
        return False
    communicated_at = parse_utc_timestamp(record.get("applied_at")) or parse_utc_timestamp(
        record.get("created")
    )
    if communicated_at is None:
        return False
    reference = now or datetime.now(UTC)
    return (reference - communicated_at) > timedelta(days=cooldown_days)


class ChannelPreference(StrEnum):
    """Recruitment channel target for App-Enforced Filters (Boss offers no native filter)."""

    ALL = "all"
    DIRECT_ONLY = "direct_only"
    HEADHUNTER_ONLY = "headhunter_only"


STATE_RANK: dict[str, int] = {
    JobRecordStatus.IGNORED: -1,
    JobRecordStatus.DIGEST_ONLY: 1,
    JobRecordStatus.JD_SAVED: 1,
    JobRecordStatus.UNMATCHED: 1,
    JobRecordStatus.MATCHED: 2,
    JobRecordStatus.APPLIED: 3,
}

TARGET_ACTION_RANK: dict[str, int] = {
    TargetAction.SAVE_JD: 1,
    TargetAction.AUTO_APPLY: 2,
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
    screened_reason: str = ""
    relaxed_by_whitelist: bool = False
    screening_audit: str = ""
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    source_task_id: str | None = None
    created: str | None = None
    updated: str | None = None

    def __post_init__(self) -> None:
        if self.title:
            self.title = clean_job_title(self.title)
        if self.recruiter_name:
            self.recruiter_name, derived_recruiter_title = split_recruiter_name(self.recruiter_name)
            if not self.recruiter_title and derived_recruiter_title:
                self.recruiter_title = derived_recruiter_title

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
        self.tags = sanitize_tags(
            self.tags,
            recruiter_name=self.recruiter_name,
            recruiter_title=self.recruiter_title,
            location=self.location or "",
            company_name=self.company_name,
            title=self.title,
        )
        if self.jd_key_requirements:
            self.jd_key_requirements = sanitize_tags(
                self.jd_key_requirements,
                recruiter_name=self.recruiter_name,
                recruiter_title=self.recruiter_title,
                location=self.location or "",
                company_name=self.company_name,
                title=self.title,
            )


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
    # App-Enforced commute distance, probed from the detail page bottom widget
    # (spec #209). None means unknown/absent — screening must fail open on it.
    commute_distance_km: float | None = None
    commute_distance_text: str = ""

    def __post_init__(self) -> None:
        if self.title:
            self.title = clean_job_title(self.title)
        if self.recruiter_name:
            self.recruiter_name, derived_recruiter_title = split_recruiter_name(self.recruiter_name)
            if not self.recruiter_title and derived_recruiter_title:
                self.recruiter_title = derived_recruiter_title

        self.is_headhunter = resolve_headhunter_channel(
            self.is_headhunter, self.recruiter_name, self.recruiter_title
        )
        if not self.digest and self.job_description:
            self.digest = extract_digest_from_jd(self.job_description)
        self.tags = sanitize_tags(
            self.tags,
            recruiter_name=self.recruiter_name or "",
            recruiter_title=self.recruiter_title or "",
            location=self.location or "",
            company_name=self.company_name,
            title=self.title,
        )


def commute_columns(posting: JobPosting | None) -> dict[str, Any]:
    """The job-record columns carrying the probed commute distance (spec #209).

    Persisted with every record regardless of the screening verdict, so the Web UI
    can show how far a job is even when the commute filter rejected it — matching
    the screening_audit trail. An absent posting (rejection before detail
    inspection) contributes the columns' empty values.
    """
    return {
        "commute_distance_km": posting.commute_distance_km if posting else None,
        "commute_distance_text": (posting.commute_distance_text if posting else "") or "",
    }


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


# A JD shorter than this carries no evaluable signal: screening or greeting from it would
# produce a meaningless score and a fabricated greeting. The card screener and the greeting
# service gate on the same precondition, so it is stated once here.
MIN_JD_CHARS = 30
UNUSABLE_JD_MARKERS = ("无详细岗位描述", "暂无详细描述", "未注明职位")


def is_substantive_jd(jd: str | None) -> bool:
    """Whether an extracted JD carries enough signal to screen or greet from."""
    text = jd or ""
    return len(text) >= MIN_JD_CHARS and text not in UNUSABLE_JD_MARKERS


#: Unambiguous staffing/agency markers. Deliberately excludes broad industry
#: words such as 咨询 or 科技, which real employers also carry (e.g. 埃森哲咨询).
HEADHUNTER_AGENCY_KEYWORDS: tuple[str, ...] = (
    "人力资源",
    "人力资本",
    "劳务派遣",
    "劳务服务",
    "人才服务",
    "人才中介",
    "人才咨询",
    "人才招聘",
    "招聘服务",
    "职业介绍",
    "企业管理咨询",
    "猎头",
    "代招",
)


def is_headhunter_agency_name(name: str | None) -> bool:
    """Check whether a company name belongs to a staffing/headhunter agency.

    A rejection card in the 仅沟通 list shows only the employer descriptor, never
    the recruiter's title, so the 猎头 signal `parse_recruiter_info` relies on is
    unavailable. The agency is instead recognised from its own registered name —
    every match here is a firm whose business *is* placing candidates elsewhere,
    so blacklisting it would punish the wrong entity.
    """
    if not name or not isinstance(name, str):
        return False

    cleaned = name.strip()
    if not cleaned:
        return False

    return any(keyword in cleaned for keyword in HEADHUNTER_AGENCY_KEYWORDS)


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
    """Policy rules for multi-stage job screening.

    Blacklists enforce deterministic one-strike rejection over compact card facets.
    The whitelist is NOT an inclusion gate and never rejects a job: it exists solely
    as relaxation tokens for App-Enforced Filters (see ``evaluate_app_enforced_filters``
    and ``evaluate_whitelist_relaxation``).
    """

    title_whitelist: list[str] = field(default_factory=list)
    title_blacklist: list[str] = field(default_factory=list)
    company_blacklist: list[str] = field(default_factory=list)
    jd_blacklist: list[str] = field(default_factory=list)
    enable_screening: bool = True
    channel_preference: str = ChannelPreference.ALL
    max_commute_distance_km: float | None = 40.0
    #: Config file `load_default` resolved this policy from; where blacklist
    #: additions are written back. Excluded from equality and serialization.
    source_path: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.channel_preference = self._normalize_channel_preference(self.channel_preference)
        self.max_commute_distance_km = self._normalize_commute_limit(self.max_commute_distance_km)

    @staticmethod
    def _normalize_channel_preference(value: Any) -> str:
        """Coerce a channel preference value to a valid ChannelPreference, defaulting to 'all'."""
        try:
            return ChannelPreference(str(value).strip().lower()).value
        except ValueError:
            return ChannelPreference.ALL.value

    @staticmethod
    def _normalize_commute_limit(value: Any) -> float | None:
        """Coerce a commute ceiling to a float in kilometers, or None when filtering is off.

        Blank strings, ``null`` sentinels and unparseable values all mean "no ceiling":
        a corrupt config must widen the filter, never silently impose an unexpected one.
        """
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            if not text or text.lower() in ("null", "none", "~"):
                return None
            try:
                return float(text)
            except ValueError:
                return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @property
    def is_commute_filter_active(self) -> bool:
        """True when the commute ceiling can actually reject something.

        Handlers gate the detail-page bottom probe on this: with the filter off,
        scrolling to find the distance widget would cost latency for nothing.
        """
        return (
            self.enable_screening
            and self.max_commute_distance_km is not None
            and self.max_commute_distance_km > 0
        )

    def should_probe_commute_distance(self, is_headhunter: bool | None) -> bool:
        """Whether this posting's detail page is worth probing for the distance widget.

        Narrower than ``is_commute_filter_active`` because the probe only buys anything
        for direct-hire postings: the platform conceals the hiring enterprise and its
        office address for headhunter roles, so ``home_tip_vf`` is never rendered for
        them and the scroll budget would be spent discovering that. An unknown channel
        (``None``) still probes — an unrecognised direct hire must not be silently
        spared distance screening.
        """
        return self.is_commute_filter_active and is_headhunter is not True

    def evaluate_commute_distance(self, commute_distance_km: float | None) -> tuple[bool, str]:
        """Evaluate the commute distance App-Enforced Filter on its own.

        Split out because the distance is only knowable from the detail page bottom,
        after the card-level channel verdict was already rendered and audited — so the
        detail-stage check must not re-report a channel violation already adjudicated.

        Fails open (passes) whenever the distance is unknown or the ceiling is disabled
        (None or <= 0): an absent widget must never reject a job.
        """
        limit = self.max_commute_distance_km
        if not self.enable_screening or limit is None or limit <= 0:
            return True, ""

        if commute_distance_km is None or commute_distance_km <= limit:
            return True, ""

        return (
            False,
            f"【App端强制过滤】距离家庭住址 {commute_distance_km:.1f}km "
            f"超过通勤上限 {limit:.1f}km",
        )

    def evaluate_app_enforced_filters(
        self,
        is_headhunter: bool = False,
        commute_distance_km: float | None = None,
    ) -> tuple[bool, str]:
        """Evaluate App-Enforced Filters the Boss platform cannot express natively.

        Currently the recruitment channel preference (direct-hire vs headhunter) and
        the commute distance ceiling. Returns (passed: bool, violation: str);
        ``violation`` is an empty string when the card satisfies all App-Enforced
        Filters, otherwise every violated condition joined by '；', which the
        Whitelist Relaxation router may still exempt. Reporting each violated
        dimension — rather than the first one checked — keeps this caller's audit
        trail equivalent to SCRAPE_JOBS', which adjudicates the two dimensions at
        different stages and can therefore record both.
        """
        if not self.enable_screening:
            return True, ""

        violations: list[str] = []

        passed, commute_violation = self.evaluate_commute_distance(commute_distance_km)
        if not passed:
            violations.append(commute_violation)

        if self.channel_preference == ChannelPreference.DIRECT_ONLY and is_headhunter:
            violations.append(
                "【App端强制过滤】猎头代招岗位违反直聘渠道偏好 (channel_preference='direct_only')"
            )
        elif self.channel_preference == ChannelPreference.HEADHUNTER_ONLY and not is_headhunter:
            violations.append(
                "【App端强制过滤】直招岗位违反猎头渠道偏好 (channel_preference='headhunter_only')"
            )

        if not violations:
            return True, ""

        return False, "；".join(violations)

    def evaluate_whitelist_relaxation(
        self,
        title: str,
        company_name: str = "",
        tags: list[str] | None = None,
        digest: str = "",
    ) -> tuple[bool, str]:
        """Evaluate Whitelist Relaxation for a card that violated an App-Enforced Filter.

        Inspects the compact card facets (title, tags, company, digest) against the
        whitelist tokens, which encode subject matter the candidate cares deeply about
        or is strong in. Returns (is_relaxed: bool, matched_token: str); an empty
        whitelist simply means nothing can be relaxed. This method grants exemptions
        only — it never rejects, and non-matching jobs pass normal evaluation when no
        App-Enforced Filter was violated.
        """
        active_whitelist = [w.strip() for w in self.title_whitelist if w and w.strip()]
        if not active_whitelist:
            return False, ""

        facets = [
            (title or "").lower(),
            (company_name or "").lower(),
            " ".join(str(t).lower() for t in (tags or [])),
            (digest or "").lower(),
        ]
        for token in active_whitelist:
            normalized = token.lower()
            if any(normalized in facet for facet in facets):
                return True, token
        return False, ""

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

        # Guardrail 1: Headhunter job posting's company name must not be blacklisted.
        # The two bases are reported distinctly: rejection triage reads cards, which
        # carry no recruiter title, so it can only ever trigger on the company name.
        if is_headhunter:
            return (
                False,
                f"【黑名单保护生效】岗位为猎头代招岗位，公司名称 '{cleaned}' 为聚合或代招渠道，禁止加入全局黑名单以避免误伤其他雇主",
            )
        if is_headhunter_agency_name(cleaned):
            return (
                False,
                f"【黑名单保护生效】'{cleaned}' 的企业名称表明其为人力资源/代招机构，禁止加入全局黑名单以避免误伤其代理的其他雇主",
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
            return (
                True,
                f"已成功将直招企业 '{cleaned}' 加入公司黑名单，后续该企业的岗位将自动过滤以节省每日投递额度",
            )
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

        # 白名单不再是准入闸门: 未命中白名单不拒绝卡片, 仅在 App 端强制过滤
        # 违例时由 evaluate_whitelist_relaxation 决定是否豁免放宽。
        return True, "通过卡片初筛"

    def to_dict(self) -> dict[str, Any]:
        return {
            "title_whitelist": self.title_whitelist,
            "title_blacklist": self.title_blacklist,
            "company_blacklist": self.company_blacklist,
            "jd_blacklist": self.jd_blacklist,
            "enable_screening": self.enable_screening,
            "channel_preference": self.channel_preference,
            "max_commute_distance_km": self.max_commute_distance_km,
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
            channel_preference=cls._normalize_channel_preference(
                data.get("channel_preference", ChannelPreference.ALL.value)
            ),
            # Absent key keeps the 40km default; an explicit null/blank means "disabled".
            max_commute_distance_km=cls._normalize_commute_limit(
                data["max_commute_distance_km"]
                if "max_commute_distance_km" in data
                else cls.__dataclass_fields__["max_commute_distance_km"].default
            ),
        )

    @classmethod
    def load_default(cls, config_path: str | Path | None = None) -> "ScreeningPolicy":
        """Load ScreeningPolicy from declarative config file with local override priority.

        Hierarchy:
        1. Explicit config_path
        2. config/settings.local.yaml
        3. config/settings.local.json
        4. config/settings.yaml
        5. config/settings.example.yaml
        6. config/screening.local.yaml (legacy fallback)
        7. config/screening.example.yaml (legacy fallback)
        """
        paths_to_check: list[Path] = []
        if config_path:
            paths_to_check.append(Path(config_path))
        else:
            try:
                from .settings import resolve_git_common_root

                root = resolve_git_common_root()
            except Exception:
                root = Path.cwd()
            candidate_rel_paths = [
                Path("config/settings.local.yaml"),
                Path("config/settings.local.yml"),
                Path("config/settings.local.json"),
                Path("config/settings.yaml"),
                Path("config/settings.example.yaml"),
                Path("config/screening.local.yaml"),
                Path("config/screening.local.yml"),
                Path("config/screening.local.json"),
                Path("config/screening.yaml"),
                Path("config/screening.example.yaml"),
            ]
            for p in candidate_rel_paths:
                paths_to_check.append(root / p if not p.is_absolute() else p)
                if not p.is_absolute():
                    paths_to_check.append(Path.cwd() / p)

        for p in paths_to_check:
            if p.is_file():
                try:
                    content = p.read_text(encoding="utf-8")
                    if p.suffix in (".yaml", ".yml"):
                        try:
                            import yaml

                            data = yaml.safe_load(content)
                        except Exception:
                            data = None
                    else:
                        import json

                        data = json.loads(content)
                    if isinstance(data, dict):
                        # Verify that screening keys exist in this config file or explicit path
                        screening_keys = (
                            "enable_screening",
                            "title_blacklist",
                            "title_whitelist",
                            "jd_blacklist",
                            "company_blacklist",
                            "channel_preference",
                            "max_commute_distance_km",
                        )
                        if config_path or any(k in data for k in screening_keys):
                            policy = cls.from_dict(data)
                            policy.source_path = str(p)
                            return policy
                except Exception:
                    pass

        return cls()

    def persist_company_blacklist(self, company_name: str) -> Path | None:
        """Write one blacklisted company back to this policy's active config file.

        Targets ``source_path`` when the policy came from a file, so the addition
        lands in the config that is actually consulted. Falls back to the writable
        screening store, and never to a checked-in ``*.example.*`` file.

        Returns the written path, or None when nothing was written — the caller
        reports that rather than claiming a persistence that did not happen.

        Two unwritable sources are treated differently, because precedence decides
        whether a redirect can take effect at all. A checked-in ``*.example.*``
        template sits *below* the unified local settings file, so redirecting there
        works and is what keeps a fresh checkout able to record a blacklist. A JSON
        local store sits *above* it, so a YAML written instead would be shadowed —
        that case reports failure instead of writing something inert.
        """
        from . import screening_config

        return screening_config.persist_company_blacklist(self, company_name)

    def save_default(self, config_path: str | Path | None = None) -> Path:
        """Persist this policy to the declarative local YAML configuration file.

        The line surgery and path guards live in :mod:`boss_agent.screening_config`;
        the policy is rules data and no longer knows how to edit a file.
        """
        from . import screening_config

        return screening_config.save_policy(self, config_path=config_path)


def _saved_search_max_jobs_default() -> int:
    """The declared ``saved_searches.max_jobs`` default, resolved lazily.

    ``boss_agent.models`` is imported *by* the broker adapter, so importing the
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
    enable_search: bool = True
    enable_filter: bool = True

    def __post_init__(self) -> None:
        # Keep nested configs in sync with top-level flags
        if hasattr(self, "search") and self.search is not None:
            self.search.enable_search = self.enable_search
        if hasattr(self, "filter") and self.filter is not None:
            self.filter.enable_filter = self.enable_filter
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
    def from_dict(
        cls, search_id: str | dict[str, Any], data: dict[str, Any] | None = None
    ) -> "SavedSearch":
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
