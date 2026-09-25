"""
boss_agent.screening
====================
Deep Candidate Screener module (ADR 0013, Spec #231).

Every screening rule the agent applies to a job — zero-token card keyword matching,
App-Enforced Filters, Whitelist Relaxation, JD semantic blacklist screening and
tailored greeting drafting — lives behind exactly two methods:

    evaluate_card(card, policy)                      -> CardScreeningVerdict
    evaluate_job(card, jd_text, profile, policy, prompt) -> JobEvaluationResult

Callers never assemble the stages themselves, so a screening policy change lands in
one file instead of five (handlers, graph nodes and service wrappers used to each
re-implement a slice of the pipeline).
"""

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from langsmith import traceable

from .matching import JobMatchGreetingService
from .memory import StructuredCandidateProfile
from .models import (
    JobPosting,
    ScreeningPolicy,
    is_substantive_jd,
    resolve_headhunter_channel,
)
from .pages import JobCardBrief

logger = logging.getLogger(__name__)

CARD_PASS_REASON = "通过卡片初筛"

UNUSABLE_JD_REASON = (
    "Job description is missing or too short ({length} chars). "
    "Full JD (tv_description) from detail page is strictly required for evaluation."
)


class CardVerdictStage(StrEnum):
    """Terminal stage of card-level (pre-detail-navigation) screening.

    Values are the screening status labels carried by `JobApplicationState.status` and
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
            )
        digest = getattr(card, "digest", "") or getattr(card, "snippet", "")
        recruiter_name = str(getattr(card, "recruiter_name", "") or "")
        recruiter_title = str(getattr(card, "recruiter_title", "") or "")
        declared_channel = getattr(card, "is_headhunter", None)
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
        )

    def to_job_posting(self, jd_text: str) -> JobPosting:
        """Materialize a JobPosting view of these facets for JD-level evaluation."""
        return JobPosting(
            title=self.title,
            company_name=self.company_name,
            salary_range=self.salary_range,
            job_description=jd_text,
            location=self.location,
            tags=list(self.tags),
            recruiter_name=self.recruiter_name,
            recruiter_title=self.recruiter_title,
            is_headhunter=self.is_headhunter,
            commute_distance_km=self.commute_distance_km,
            commute_distance_text=self.commute_distance_text,
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


class CandidateScreener:
    """Deep module owning every candidate screening rule and greeting decision.

    Dependencies are injected so screening can be exercised over pure domain data
    (``JobCardBrief`` / ``ScreeningPolicy`` / ``StructuredCandidateProfile``) with a
    mockable LLM client — no Appium driver, no virtual device, no database.
    """

    def __init__(
        self,
        llm_client: Any | None = None,
        matching_service: JobMatchGreetingService | None = None,
        greeting_prompt: str | None = None,
    ) -> None:
        self.llm_client = llm_client
        self._matching_service = matching_service
        self.greeting_prompt = greeting_prompt

    # ------------------------------------------------------------------
    # Stage 1: card screening (zero-token, driver-free)
    # ------------------------------------------------------------------
    @traceable(name="CandidateScreener.evaluate_card", run_type="chain")
    def evaluate_card(
        self,
        card: JobCardBrief | JobPosting | dict[str, Any],
        policy: ScreeningPolicy | dict[str, Any] | None = None,
    ) -> CardScreeningVerdict:
        """Screen a card against keywords, App-Enforced Filters and Whitelist Relaxation.

        Runs in a single step because the three rules are one decision: a card that
        violates an App-Enforced Filter is only rejected if Whitelist Relaxation does
        not rescue it, and splitting them across callers is what let the two worker
        handlers drift apart.
        """
        resolved = _resolve_policy(policy)
        facets = CardFacets.from_card(card)

        passed, reason = resolved.matches_card_keywords(
            title=facets.title,
            company_name=facets.company_name,
            tags=facets.tags,
            digest=facets.digest,
        )
        if not passed:
            return CardScreeningVerdict.rejected_by_keywords(reason)

        app_pass, violation = resolved.evaluate_app_enforced_filters(
            is_headhunter=facets.is_headhunter,
            commute_distance_km=facets.commute_distance_km,
        )
        if app_pass:
            return CardScreeningVerdict.approved()

        relaxed, matched_token = resolved.evaluate_whitelist_relaxation(
            title=facets.title,
            company_name=facets.company_name,
            tags=facets.tags,
            digest=facets.digest,
        )
        if relaxed:
            return CardScreeningVerdict.approved(
                relaxed_by_whitelist=True,
                matched_token=matched_token,
                app_rule_violation=violation,
            )
        return CardScreeningVerdict.rejected_by_app_rule(violation)

    # ------------------------------------------------------------------
    # Stage 2: full-JD evaluation and greeting drafting
    # ------------------------------------------------------------------
    @traceable(name="CandidateScreener.evaluate_job", run_type="chain")
    def evaluate_job(
        self,
        card: JobCardBrief | JobPosting | dict[str, Any],
        jd_text: str,
        profile: StructuredCandidateProfile | dict[str, Any] | None = None,
        policy: ScreeningPolicy | dict[str, Any] | None = None,
        prompt: str | None = None,
        *,
        draft_greeting: bool = True,
    ) -> JobEvaluationResult:
        """Validate the extracted JD, run semantic screening, and draft the greeting.

        ``draft_greeting=False`` keeps the deterministic JD-length validation and the
        semantic blacklist screen but skips greeting synthesis — used by save-only
        runs, where a greeting would cost tokens nobody reads.
        """
        resolved = _resolve_policy(policy)
        facets = CardFacets.from_card(card)
        jd = (jd_text or "").strip() if isinstance(jd_text, str) else ""

        # A JD too thin to carry signal is never worth a screening token, let alone a
        # greeting: report it as unavailable instead of judging (and greeting) nothing.
        if not _jd_is_substantive(jd):
            reason = UNUSABLE_JD_REASON.format(length=len(jd))
            return JobEvaluationResult(
                passed=False,
                stage=JobVerdictStage.JD_UNAVAILABLE,
                reason=reason,
                error_message=reason,
            )

        screen_pass, screen_reason = self._screen_jd_semantics(
            jd_text=jd,
            card_title=facets.title,
            company_name=facets.company_name,
            policy=resolved,
        )
        if not screen_pass:
            return JobEvaluationResult(
                passed=False,
                stage=JobVerdictStage.FILTERED_BY_DEEP_SCREENER,
                reason=screen_reason,
            )

        if not draft_greeting:
            return JobEvaluationResult(
                passed=True,
                stage=JobVerdictStage.PASSED,
                reason=screen_reason,
            )

        profile_obj = _resolve_profile(profile)
        try:
            match = self._greeting_service().evaluate_and_draft_greeting(
                job=facets.to_job_posting(jd),
                profile=profile_obj,
                greeting_prompt=prompt or self.greeting_prompt,
                screening_policy=resolved,
            )
        except ValueError as e:
            logger.warning("Greeting drafting skipped due to invalid JD: %s", e)
            return JobEvaluationResult(
                passed=False,
                stage=JobVerdictStage.JD_UNAVAILABLE,
                reason=str(e),
                error_message=str(e),
            )

        return JobEvaluationResult(
            passed=True,
            stage=JobVerdictStage.PASSED,
            reason=screen_reason,
            match_score=match.match_score,
            match_reasons=list(match.match_reasons),
            jd_key_requirements=list(match.jd_key_requirements),
            greeting_message=match.greeting_message,
        )

    @traceable(name="CandidateScreener.screen_jd_semantics", run_type="chain")
    def _screen_jd_semantics(
        self,
        jd_text: str,
        card_title: str = "",
        company_name: str = "",
        policy: ScreeningPolicy | None = None,
    ) -> tuple[bool, str]:
        """Evaluate JD text against the screening policy's blacklist without the resume.

        The whitelist has zero veto power at this stage: it exists only as App-Enforced
        Filter relaxation tokens and never appears in the screening prompt.
        """
        if not policy or not policy.enable_screening:
            return True, "筛选策略未启用"

        if not jd_text or not jd_text.strip():
            return True, "无详细JD文本，跳过语义精筛"

        blacklist = sorted(
            {b.strip() for b in policy.jd_blacklist + policy.title_blacklist if b and b.strip()}
        )
        if not blacklist:
            return True, "未配置黑名单，JD语义精筛默认放行（白名单对JD正文零否决权）"

        system_prompt = (
            "你是一名严谨的岗位精筛助手。你的唯一任务是依据【黑名单筛选准则】，深度阅读招聘岗位详情(JD)，"
            "判断该岗位是否应当被淘汰。\n"
            "【筛选准则】：\n"
            f"- 黑名单关键词(语义一票否决): {blacklist}\n\n"
            "【判决规则】：\n"
            "1. 黑名单关键词主要用于过滤岗位核心性质与主技术栈（如岗位本质是纯Java开发、微服务业务架构、销售外包或人力驻场等）。"
            "若黑名单关键词仅在长篇JD中作为协作方、技术背景提及、次要了解项或否定句出现（如“配合Java团队”、“了解微服务者优先”但主体是Agent/Python岗位），"
            "严禁误伤，应判决 pass: true；只有当黑名单主题构成了该岗位的核心职责或主要技术栈时，才判决 pass: false。\n"
            "2. 判决仅依据上述黑名单语义评估：JD未触犯黑名单即判决 pass: true，无需JD与任何白名单或兴趣方向词相关联。\n"
            "3. 严格输出标准 JSON 格式：{\"pass\": true或false, \"reason\": \"50字以内的判定简述\"}。"
        )

        user_prompt = (
            f"职位名称: {card_title}\n"
            f"招聘公司: {company_name}\n"
            f"岗位描述(JD):\n{jd_text}\n\n"
            '请严格输出 JSON: {"pass": true/false, "reason": "判定原因"}'
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        client = self.llm_client
        if not client:
            from droid_agent_core.llm import OpenAIChatClient

            client = self.llm_client = OpenAIChatClient()

        try:
            res = client.chat_completion_json(messages)
            passed = bool(res.get("pass", True))
            reason = str(res.get("reason", "精筛完成"))
            return passed, reason
        except Exception as e:
            return True, f"LLM精筛调用异常，降级放行: {e}"

    def _greeting_service(self) -> JobMatchGreetingService:
        """Lazily build the greeting service so save-only runs never load LLM config."""
        if self._matching_service is None:
            self._matching_service = JobMatchGreetingService(llm_client=self.llm_client)
        return self._matching_service


def _jd_is_substantive(jd: str) -> bool:
    """Whether an extracted JD carries enough signal to greet from."""
    return is_substantive_jd(jd)


def _resolve_policy(policy: ScreeningPolicy | dict[str, Any] | None) -> ScreeningPolicy:
    if isinstance(policy, ScreeningPolicy):
        return policy
    return ScreeningPolicy.from_dict(policy)


def _resolve_profile(
    profile: StructuredCandidateProfile | dict[str, Any] | None,
) -> StructuredCandidateProfile | None:
    if isinstance(profile, StructuredCandidateProfile):
        return profile
    if isinstance(profile, dict) and profile:
        return StructuredCandidateProfile.from_dict(profile)
    return None
