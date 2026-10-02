"""
boss_agent.identifier_helpers
=============================
Pure identifier, recruiter normalization, and classification helpers (Issue #311, Spec #303).
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from boss_agent.enums import ChatButtonState
from boss_agent.keyword_constants import (
    _JD_HEADER_PREFIX_RE,
    _JD_HEADER_STANDALONE_RE,
    _RECRUITER_SEPARATOR_RE,
    CLOSED_BUTTON_TEXTS,
    COMMON_COMPOUND_SURNAMES,
    COMMON_TECH_TAGS,
    COMMUNICATED_BUTTON_TEXTS,
    COMPANY_INDICATOR_KEYWORDS,
    EDUCATION_KEYWORDS,
    EXPERIENCE_KEYWORDS,
    GENERIC_ROLE_TOKENS,
    GREETING_SOURCE_HUMAN,
    HEADHUNTER_AGENCY_KEYWORDS,
    INVALID_COMPANY_NAMES,
    KNOWN_CITIES,
    MIN_JD_CHARS,
    PLATFORM_BADGE_MARKERS,
    RECRUITER_SEPARATORS,
    RECRUITER_TITLE_KEYWORDS,
    TRUNCATED_JD_MARKERS,
    UNCONTACTED_BUTTON_TEXTS,
    UNUSABLE_JD_MARKERS,
)


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


def parse_recruiter_title(raw: str | None) -> str:
    """Parse a recruiter name into an appropriate Chinese salutation title.

    Supported patterns:
    - 'xx 女士' (e.g. '张女士', '张女士 · HR') -> '张女士'
    - 'xx 先生' (e.g. '钟先生', '钟先生 · 猎头顾问') -> '钟先生'
    - 'xxx(真名)' (e.g. '张伟', '诸葛孔明', '欧阳六') -> recognizes single or compound surname -> '张总', '诸葛总', '欧阳总'
    - 'xx 总' (e.g. '张总', '诸葛总') -> '张总', '诸葛总'
    - Pure English, digits, generic roles, or missing/abnormal names -> '' (falls back to generic greeting)
    """
    if not raw:
        return ""
    text = str(raw).strip()
    if not text:
        return ""

    # 1. Normalize spaces before gendered suffixes e.g. "张 女士" -> "张女士"
    text = re.sub(r"\s*(女士|先生)", r"\1", text)

    # 2. Strip Boss separator and attached title (e.g. "钟先生 · 猎头顾问" -> "钟先生")
    name = split_recruiter_name(text)[0]

    # 3. Strip brackets/parentheses (e.g. "张女士(HR)", "李女士【招聘】")
    name = re.sub(r"[\(（\[【].*?[\)）\]】]", "", name).strip()

    # 4. Strip trailing role tokens separated by whitespace or delimiter
    name = re.split(r"[\s/|_\-]+", name)[0].strip()

    if not name:
        return ""

    # Check generic role tokens
    if name.lower() in GENERIC_ROLE_TOKENS:
        return ""

    # Must contain only Chinese characters (English/digits/abnormal -> fallback)
    if not re.match(r"^[\u4e00-\u9fa5]+$", name):
        return ""

    # Check "xx女士", "xx先生", "xx总"
    if name.endswith("女士"):
        if len(name) >= 3:
            return name
        return ""
    if name.endswith("先生"):
        if len(name) >= 3:
            return name
        return ""
    if name.endswith("总"):
        if len(name) in (2, 3):
            return name
        return ""

    # Real name (2-4 characters):
    if 2 <= len(name) <= 4:
        if len(name) >= 3 and name[:2] in COMMON_COMPOUND_SURNAMES:
            return f"{name[:2]}总"
        return f"{name[0]}总"

    return ""


def format_recruiter_greeting_prefix(raw: str | None) -> str:
    """Format the opening greeting prefix for a recruiter.

    - 'xx 女士' -> 'xx女士您好,幸会!'
    - 'xx 先生' -> 'xx先生您好,幸会!'
    - 'xxx(真名)' -> 'x总您好,幸会!' or 'xx总您好,幸会!' (compound surname)
    - Fallback -> '您好,幸会!'
    """
    title = parse_recruiter_title(raw)
    if title:
        return f"{title}您好,幸会!"
    return "您好,幸会!"


def compute_job_fingerprint(company_name: str, title: str, recruiter_name: str) -> str:
    """Compute normalized SHA-256 fingerprint for a job card using the canonical 3 fields."""
    norm_comp = (company_name or "").strip()
    norm_title = clean_job_title(title)
    norm_recruiter = normalize_recruiter_name(recruiter_name)
    raw = f"{norm_comp}::{norm_title}::{norm_recruiter}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
    """Whether a posting belongs to a genuine direct-hire enterprise for 同企避嫌 purposes.

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


def commute_columns(posting: Any) -> dict[str, Any]:
    """The job-record columns carrying the probed commute distance (spec #209).

    Persisted with every record regardless of the screening verdict, so the Web UI
    can show how far a job is even when the commute filter rejected it — matching
    the screening_audit trail. An absent posting (rejection before detail
    inspection) contributes the columns' empty values.
    """
    km = getattr(posting, "commute_distance_km", None) if posting else None
    text = getattr(posting, "commute_distance_text", "") if posting else ""
    return {
        "commute_distance_km": km,
        "commute_distance_text": text or "",
    }


def is_substantive_jd(jd: str | None) -> bool:
    """Whether an extracted JD carries enough signal to screen or greet from."""
    text = jd or ""
    return len(text) >= MIN_JD_CHARS and text not in UNUSABLE_JD_MARKERS


def jd_is_truncated(jd: str | None) -> bool:
    """Whether a job description stops short of the full text on screen."""
    text = (jd or "").strip()
    return any(marker in text for marker in TRUNCATED_JD_MARKERS) or text.endswith("...")


def jd_is_usable_on_file(jd: str | None) -> bool:
    """Whether a JD a record already holds is as good as reading the page again (#301).

    The two judgements a freshly extracted JD is put through, applied to the stored text:
    enough signal to work from, and not truncated. Not a third rule.
    """
    text = (jd or "").strip()
    return bool(text) and is_substantive_jd(text) and not jd_is_truncated(text)


def greeting_is_human(record: dict | None) -> bool:
    """Whether a Job Record's greeting was written by a human rather than drafted by the agent.

    Unknown provenance is not human provenance. A legacy record — and a record whose
    ``greeting_source`` arrived as something else entirely — keeps today's behaviour of
    being drafted for, so shipping the field cannot silently freeze an old agent draft in
    place as if somebody had approved it.
    """
    if not record:
        return False
    text = (record.get("greeting_message") or "").strip()
    return bool(text) and str(record.get("greeting_source") or "") == GREETING_SOURCE_HUMAN


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
    return bool(
        re.match(
            r"^(?:知名|头部|大型|中型|小型|外资|民营|上市|创业|初创)"
            r"(?:互联网|科技|金融|量化|医疗|AI|人工智能)?"
            r"(?:公司|企业|集团|机构|团队|厂商|大厂|外企)$",
            cleaned,
        )
    )
