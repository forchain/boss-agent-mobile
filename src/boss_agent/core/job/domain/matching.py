"""
boss_agent.core.job.domain.matching
===================================
Pure job-match and greeting formatting rules.

Everything here is a decision about text: the recruiter salutation, the offline
fallback evaluation, filter rendering and the result shape. The LLM-backed
:class:`~boss_agent.core.job.use_cases.matching.JobMatchGreetingService` composes these
rules with an injected client and never owns them.
"""

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from boss_agent.core.candidate.domain.candidate_profile import CandidateProfile
from boss_agent.core.job.domain.job_entities import JobPosting
from boss_agent.core.shared.identifier_helpers import (
    extract_tags_from_text,
    format_recruiter_greeting_prefix,
)

_GREETING_OPENING_RE = re.compile(
    r"^(?:(?:尊敬的)?(?:[^，,！!。\s\n]{1,8})?[，,\s]*)?"
    r"(?:您好|你好|幸会|早上好|下午好|打扰了)[，,！!。\s]*"
    r"(?:幸会[！!，,\s]*)?",
    re.IGNORECASE,
)


def ensure_greeting_prefix(greeting: str, recruiter_name: str | None) -> str:
    """Guarantee that greeting_message strictly starts with the dynamic recruiter salutation prefix.

    Prefix rules:
    - 'xx 女士' -> 'xx女士您好,幸会!'
    - 'xx 先生' -> 'xx先生您好,幸会!'
    - 'xxx(真名)' -> 'x总您好,幸会!' / 'xx总您好,幸会!'
    - Missing / English / Abnormal -> '您好,幸会!'

    Any redundant or existing greeting header is stripped and normalized to the canonical prefix.
    """
    expected_prefix = format_recruiter_greeting_prefix(recruiter_name)
    if not greeting or not greeting.strip():
        return expected_prefix

    text = greeting.strip()
    if text.startswith(expected_prefix):
        return text

    # Strip any leading greeting or salutation
    m = _GREETING_OPENING_RE.match(text)
    rest = text[len(m.group(0)) :].lstrip("，,、 \t\n") if m else text

    return f"{expected_prefix}{rest}"


@dataclass
class MatchGreetingResult:
    """Output evaluation combining match score, alignment points, and greeting draft."""

    match_score: int = 50
    match_reasons: list[str] = field(default_factory=list)
    jd_key_requirements: list[str] = field(default_factory=list)
    greeting_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MatchGreetingResult":
        return cls(
            match_score=int(data.get("match_score") or 50),
            match_reasons=data.get("match_reasons") or [],
            jd_key_requirements=data.get("jd_key_requirements") or [],
            greeting_message=data.get("greeting_message") or "",
        )


# The offline path scores a JD the way the dashboard's own fallback does — a fixed floor plus
# a per-requirement bonus, capped — so the two layers of the same fallback ladder agree on
# what a degraded evaluation looks like. What it adds is that the requirements are the ones
# the JD actually names, drawn from the shared `COMMON_TECH_TAGS` taxonomy rather than a
# handful of hardcoded sentences.
_OFFLINE_SCORE_FLOOR = 85
_OFFLINE_SCORE_PER_REQUIREMENT = 4
_OFFLINE_SCORE_CEILING = 98
_GENERIC_REQUIREMENT = "具备扎实的工程研发能力与快速业务落地实战经验"


def offline_match_result(
    job: JobPosting, profile: CandidateProfile | None = None
) -> MatchGreetingResult:
    """Evaluate a JD deterministically, with no LLM call and no credentials.

    Every caller of the match evaluation degrades to *something* when the model is
    unreachable — an unset key, a CI runner, a provider outage. A constant score plus a
    placeholder requirement is the one answer that helps nobody: it tells the operator the
    posting scored 50 for a reason that has nothing to do with the posting. This reads the
    JD instead, so the degraded answer is at least true about the JD it was given.
    """
    prefix = format_recruiter_greeting_prefix(job.recruiter_name)
    tags = extract_tags_from_text(job.job_description or "")

    requirements = [f"JD 明确要求：{tag}" for tag in tags] or [_GENERIC_REQUIREMENT]
    score = min(
        _OFFLINE_SCORE_FLOOR + _OFFLINE_SCORE_PER_REQUIREMENT * len(tags),
        _OFFLINE_SCORE_CEILING,
    )

    reasons = ["按 JD 关键词离线评估，未调用大模型"]
    if profile is not None:
        skills = {str(s).strip().lower() for s in (profile.core_skills or []) if s}
        covered = [t for t in tags if t.lower() in skills]
        if covered:
            reasons.append(f"候选人技能覆盖 JD 要点：{'、'.join(covered)}")
        else:
            reasons.append("候选人技能未覆盖 JD 识别出的要点，本结论仅供参考")

    focus = tags[0] if tags else "核心工程能力"
    greeting = (
        f"{prefix}看到贵司正在招聘【{job.title}】，JD 中对“{focus}”的明确诉求正是我过往"
        f"深耕的方向，已有从需求拆解到落地交付的完整实战经验。期待能就该岗位的具体挑战"
        f"与您进一步沟通！"
    )

    return MatchGreetingResult(
        match_score=score,
        match_reasons=reasons,
        jd_key_requirements=requirements,
        greeting_message=greeting,
    )


def format_search_filter(search_filter: dict[str, Any] | Any | None) -> str:
    """Format search filter conditions into a readable structured text block."""
    if not search_filter:
        return "无"
    if hasattr(search_filter, "to_dict"):
        search_filter = search_filter.to_dict()
    if not isinstance(search_filter, dict):
        return str(search_filter)

    parts: list[str] = []
    edu = search_filter.get("education")
    if edu and str(edu).strip() and str(edu).strip() != "不限":
        parts.append(f"目标学历: {edu}")
    sal = search_filter.get("salary")
    if sal and str(sal).strip() and str(sal).strip() != "不限":
        parts.append(f"薪资要求: {sal}")
    exp = search_filter.get("experience")
    if exp and str(exp).strip() and str(exp).strip() != "不限":
        parts.append(f"经验要求: {exp}")
    act = search_filter.get("activity")
    if act and str(act).strip() and str(act).strip() != "不限":
        parts.append(f"活跃度: {act}")
    scales = search_filter.get("company_scales")
    if isinstance(scales, list) and scales:
        parts.append(f"公司规模: {', '.join(str(s) for s in scales)}")
    inds = search_filter.get("industries")
    if isinstance(inds, list) and inds:
        parts.append(f"行业要求: {', '.join(str(i) for i in inds)}")

    handled_keys = {
        "education",
        "salary",
        "experience",
        "activity",
        "company_scales",
        "industries",
        "enable_filter",
    }
    for k, v in search_filter.items():
        if k not in handled_keys and v:
            parts.append(f"{k}: {v}")

    return "；".join(parts) if parts else "无"
