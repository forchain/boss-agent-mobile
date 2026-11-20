"""
boss_agent.core.job.domain.screening
====================================
Screening verdicts and the pure rules that produce them (ADR 0013, Spec #231).

Every screening rule the agent applies to a job — zero-token card keyword matching,
App-Enforced Filters, Whitelist Relaxation, JD semantic blacklist screening and
tailored greeting drafting — is decided at two seams:

    CandidateScreener.evaluate_card(card, policy)                      -> CardScreeningVerdict
    CandidateScreener.evaluate_job(card, jd_text, profile, policy, prompt) -> JobEvaluationResult

The apply-facing use case lives in ``core.job.use_cases.screening``; this module holds
the verdict types and the pure coercion/validation rules they are built from, so a reader
can test the vocabulary without an LLM client.

Callers never assemble the stages themselves, so a screening policy change lands in
one file instead of five (handlers, graph nodes and service wrappers used to each
re-implement a slice of the pipeline).
"""

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from boss_agent.core.candidate.domain.candidate_profile import CandidateProfile
from boss_agent.core.job.domain.job_entities import JobPosting
from boss_agent.core.job.domain.screening_policy import ScreeningPolicy
from boss_agent.core.shared.identifier_helpers import is_substantive_jd, resolve_headhunter_channel

logger = logging.getLogger(__name__)

CARD_PASS_REASON = "通过卡片初筛"

UNUSABLE_JD_REASON = (
    "Job description is missing or too short ({length} chars). "
    "Full JD (tv_description) from detail page is strictly required for evaluation."
)


class CardVerdictStage(StrEnum):
    """Terminal stage of card-level (pre-detail-navigation) screening.

    Values are the screening status labels carried by `CardScreeningVerdict.stage` and
    logged to the task stream. They are *not* persisted as job-record statuses: a
    rejected card is written as `JobRecordStatus.IGNORED`, with the stage in
    `screened_reason`.
    """

    PASSED = "keyword_passed"
    RELAXED = "relaxed_by_whitelist"
    FILTERED_BY_KEYWORD = "filtered_by_keyword"
    FILTERED_BY_APP_RULE = "filtered_by_app_rule"


class JobVerdictStage(StrEnum):
    """Terminal stage of full-JD evaluation."""

    PASSED = "passed"
    FILTERED_BY_DEEP_SCREENER = "filtered_by_deep_screener"
    JD_UNAVAILABLE = "jd_unavailable"


@dataclass(frozen=True)
class CardFacets:
    """The compact, already-extracted facets a card-level decision may consult.

    Screening is deliberately blind to everything else on the card (coordinates, view
    handles, deep links), which is what makes card screening testable without a driver.
    """

    title: str = ""
    company_name: str = ""
    tags: list[str] = field(default_factory=list)
    digest: str = ""
    recruiter_name: str = ""
    recruiter_title: str = ""
    salary_range: str = ""
    location: str = ""
    is_headhunter: bool = False
    commute_distance_km: float | None = None
    commute_distance_text: str = ""
    search_filter: dict[str, Any] | None = None

    @classmethod
    def from_card(cls, card: Any) -> "CardFacets":
        """Coerce a JobCardBrief, JobPosting or serialized card dict into facets."""
        if card is None:
            return cls()
        if isinstance(card, cls):
            return card
        if isinstance(card, dict):
            recruiter_name = str(card.get("recruiter_name") or "")
            recruiter_title = str(card.get("recruiter_title") or "")
            raw_filter = card.get("search_filter")
            if hasattr(raw_filter, "to_dict"):
                raw_filter = raw_filter.to_dict()
            return cls(
                title=str(card.get("title") or ""),
                company_name=str(card.get("company_name") or ""),
                tags=list(card.get("tags") or []),
                digest=str(card.get("digest") or card.get("snippet") or ""),
                recruiter_name=recruiter_name,
                recruiter_title=recruiter_title,
                salary_range=str(card.get("salary_range") or ""),
                location=str(card.get("location") or ""),
                is_headhunter=resolve_headhunter_channel(
                    card.get("is_headhunter"), recruiter_name, recruiter_title
                ),
                commute_distance_km=card.get("commute_distance_km"),
                commute_distance_text=str(card.get("commute_distance_text") or ""),
                search_filter=raw_filter,
            )
        digest = getattr(card, "digest", "") or getattr(card, "snippet", "")
        recruiter_name = str(getattr(card, "recruiter_name", "") or "")
        recruiter_title = str(getattr(card, "recruiter_title", "") or "")
        declared_channel = getattr(card, "is_headhunter", None)
        raw_filter = getattr(card, "search_filter", None)
        if hasattr(raw_filter, "to_dict"):
            raw_filter = raw_filter.to_dict()
        return cls(
            title=str(getattr(card, "title", "") or ""),
            company_name=str(getattr(card, "company_name", "") or ""),
            tags=list(getattr(card, "tags", None) or []),
            digest=str(digest or ""),
            recruiter_name=recruiter_name,
            recruiter_title=recruiter_title,
            salary_range=str(getattr(card, "salary_range", "") or ""),
            location=str(getattr(card, "location", "") or ""),
            is_headhunter=resolve_headhunter_channel(
                declared_channel, recruiter_name, recruiter_title
            ),
            commute_distance_km=getattr(card, "commute_distance_km", None),
            commute_distance_text=str(getattr(card, "commute_distance_text", "") or ""),
            search_filter=raw_filter,
        )

    def to_job_posting(
        self,
        jd_text: str,
        *,
        search_filter: dict[str, Any] | Any | None = None,
    ) -> JobPosting:
        """Materialize a JobPosting view of these facets for JD-level evaluation."""
        filter_dict = search_filter if search_filter is not None else self.search_filter
        if hasattr(filter_dict, "to_dict"):
            filter_dict = filter_dict.to_dict()
        return JobPosting(
            title=self.title,
            company_name=self.company_name,
            salary_range=self.salary_range,
            job_description=jd_text,
            digest=self.digest,
            location=self.location,
            tags=list(self.tags),
            recruiter_name=self.recruiter_name,
            recruiter_title=self.recruiter_title,
            is_headhunter=self.is_headhunter,
            commute_distance_km=self.commute_distance_km,
            commute_distance_text=self.commute_distance_text,
            search_filter=filter_dict,
        )


@dataclass(frozen=True)
class CardScreeningVerdict:
    """Outcome of card-level screening (stage 1: everything decided without a JD).

    ``reason`` is the audit line for the ordinary path — the pass message or the
    blacklist hit that rejected the card — and is what the Job Record Store keeps as
    ``screened_reason``. ``screening_audit`` records the exceptional path only (an
    App-Enforced Filter violation and any Whitelist Relaxation that exempted it), so a
    reader can tell a plain pass from a rescued one.
    """

    passed: bool
    stage: CardVerdictStage
    reason: str = ""
    app_rule_pass: bool = True
    app_rule_violation: str = ""
    relaxed_by_whitelist: bool = False
    relaxation_reason: str = ""
    matched_token: str = ""
    screening_audit: str = ""

    @classmethod
    def rejected_by_keywords(cls, reason: str) -> "CardScreeningVerdict":
        return cls(
            passed=False,
            stage=CardVerdictStage.FILTERED_BY_KEYWORD,
            reason=reason,
        )

    @classmethod
    def rejected_by_app_rule(cls, violation: str) -> "CardScreeningVerdict":
        return cls(
            passed=False,
            stage=CardVerdictStage.FILTERED_BY_APP_RULE,
            reason=violation,
            app_rule_pass=False,
            app_rule_violation=violation,
            screening_audit=f"App端强制过滤违例: {violation}",
        )

    @classmethod
    def approved(
        cls,
        *,
        relaxed_by_whitelist: bool = False,
        matched_token: str = "",
        app_rule_violation: str = "",
    ) -> "CardScreeningVerdict":
        if not relaxed_by_whitelist:
            return cls(
                passed=True,
                stage=CardVerdictStage.PASSED,
                reason=CARD_PASS_REASON,
                app_rule_violation=app_rule_violation,
            )
        return cls(
            passed=True,
            stage=CardVerdictStage.RELAXED,
            reason=CARD_PASS_REASON,
            app_rule_pass=False,
            app_rule_violation=app_rule_violation,
            relaxed_by_whitelist=True,
            matched_token=matched_token,
            relaxation_reason=(
                f"【白名单放宽】命中兴趣/专长关键词 '{matched_token}'，"
                f"豁免 App 端强制过滤违例: {app_rule_violation}"
            ),
            screening_audit=(
                f"App端强制过滤违例: {app_rule_violation}；"
                f"【白名单放宽】命中关键词 '{matched_token}'，予以豁免"
            ),
        )


@dataclass(frozen=True)
class JobEvaluationResult:
    """Outcome of full-JD evaluation and greeting drafting (stage 2)."""

    passed: bool
    stage: JobVerdictStage
    reason: str = ""
    match_score: int = 0
    match_reasons: list[str] = field(default_factory=list)
    jd_key_requirements: list[str] = field(default_factory=list)
    greeting_message: str = ""
    error_message: str = ""


@dataclass(frozen=True)
class ScreeningVerdict:
    """Outcome of re-evaluating a job under human critique (ADR 0010, Spec #340)."""

    approved: bool
    reason: str
    stage: str = ""


def _coerce_screening_verdict(res: dict[str, Any] | Any, reason: str = "") -> bool:
    """Coerce screening verdict to bool, supporting 'approved', 'qualified', and legacy 'pass' keys."""
    raw_val = True
    if isinstance(res, dict):
        if "approved" in res:
            raw_val = res["approved"]
        elif "qualified" in res:
            raw_val = res["qualified"]
        elif "pass" in res:
            raw_val = res["pass"]
    else:
        raw_val = res

    if isinstance(raw_val, bool):
        verdict = raw_val
    elif isinstance(raw_val, (int, float)):
        verdict = bool(raw_val)
    elif isinstance(raw_val, str):
        val = raw_val.strip().lower()
        if val in ("false", "0", "fail", "no", "淘汰", "不合格", "被淘汰", "拒绝", "reject"):
            verdict = False
        else:
            verdict = True
    elif raw_val is None:
        verdict = False
    else:
        verdict = bool(raw_val)

    if not verdict and reason:
        # Defense-in-depth: if the LLM explicitly concluded the job did not hit the blacklist
        # or declared it qualified/approved, rescue against boolean inversion.
        has_negated_blacklist = bool(
            re.search(r"(?:未|不)(?:涉及|触犯|命中|包含|存在).*?黑名单", reason)
            or re.search(r"未触犯|未命中|未违规|无违例", reason)
        )
        has_positive_verdict = bool(
            re.search(r"(?:通过|合格|保留|予以放行|approved|qualified)", reason, re.IGNORECASE)
            or re.search(r"\bpass\b", reason, re.IGNORECASE)
        )
        has_unnegated_rejection = bool(
            re.search(r"(?<!未)(?<!不)(?:命中|触犯|属于|触发).*?黑名单", reason)
            or re.search(r"不合格|不符合|予以淘汰|应予淘汰|淘汰", reason)
        )
        if (has_negated_blacklist or has_positive_verdict) and not has_unnegated_rejection:
            logger.warning(
                "LLM screening verdict=False contradicts positive reason ('%s'); overriding to True",
                reason,
            )
            return True

    return verdict


def _jd_is_substantive(jd: str) -> bool:
    """Whether an extracted JD carries enough signal to greet from."""
    return is_substantive_jd(jd)


def _resolve_policy(policy: ScreeningPolicy | dict[str, Any] | None) -> ScreeningPolicy:
    if isinstance(policy, ScreeningPolicy):
        return policy
    return ScreeningPolicy.from_dict(policy)


def _resolve_profile(
    profile: CandidateProfile | dict[str, Any] | None,
) -> CandidateProfile | None:
    if isinstance(profile, CandidateProfile):
        return profile
    if isinstance(profile, dict) and profile:
        return CandidateProfile.from_dict(profile)
    return None
