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
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from langsmith import traceable

from .matching import JobMatchGreetingService
from .memory import StructuredCandidateProfile
from .models import (
    JobCardBrief,
    JobPosting,
    ScreeningPolicy,
    is_substantive_jd,
    resolve_headhunter_channel,
)

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
        screening_prompt: str | None = None,
    ) -> None:
        self.llm_client = llm_client
        self._matching_service = matching_service
        self.greeting_prompt = greeting_prompt
        self.screening_prompt = screening_prompt

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
            location=facets.location,
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
        screening_prompt: str | None = None,
        search_filter: dict[str, Any] | Any | None = None,
    ) -> JobEvaluationResult:
        """Validate the extracted JD, run semantic screening, and draft the greeting.

        ``draft_greeting=False`` keeps the deterministic JD-length validation and the
        semantic blacklist screen but skips greeting synthesis — used by save-only
        runs, where a greeting would cost tokens nobody reads.
        """
        resolved = _resolve_policy(policy)
        facets = CardFacets.from_card(card)
        effective_search_filter = (
            search_filter
            if search_filter is not None
            else (
                facets.search_filter
                or (
                    card.get("search_filter")
                    if isinstance(card, dict)
                    else getattr(card, "search_filter", None)
                )
            )
        )
        if hasattr(effective_search_filter, "to_dict"):
            effective_search_filter = effective_search_filter.to_dict()
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
            screening_prompt=screening_prompt,
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
                job=facets.to_job_posting(jd, search_filter=effective_search_filter),
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
        screening_prompt: str | None = None,
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

        prompt_template = screening_prompt or self.screening_prompt
        if prompt_template is None:
            from .screening_prompt import load_screening_prompt

            try:
                prompt_template = load_screening_prompt()
            except FileNotFoundError:
                prompt_template = ""

        if prompt_template:
            system_prompt = (
                f"{prompt_template}\n\n"
                "【系统动态约束】：\n"
                f"- 当前配置的黑名单关键词(语义一票否决): {blacklist}\n\n"
                "【输出格式硬性约定】：\n"
                '严格输出标准 JSON 格式：{"approved": true(合格保留)或false(命中黑名单淘汰), "reason": "50字以内的判定简述，明确写【合格保留】或【淘汰：具体原因】，严禁使用具有中英二义性的 pass 词汇"}。'
            )
        else:
            system_prompt = (
                "你是一名严谨的岗位精筛助手。你的唯一任务是依据【黑名单筛选准则】，深度阅读招聘岗位详情(JD)，"
                "判断该岗位是否【合格保留】。\n"
                "【筛选准则】：\n"
                f"- 黑名单关键词(语义一票否决): {blacklist}\n\n"
                "【判决规则】：\n"
                "1. 黑名单关键词主要用于过滤岗位核心性质与主技术栈（如岗位本质是纯Java开发、微服务业务架构、销售外包或人力驻场等）。"
                "若黑名单关键词仅在长篇JD中作为协作方、技术背景提及、次要了解项或否定句出现（如“配合Java团队”、“了解微服务者优先”但主体是Agent/Python岗位），"
                "严禁误伤，属于合格岗位，必须判决 approved: true；只有当黑名单主题构成了该岗位的核心职责或主要技术栈时，才属于淘汰岗位，判决 approved: false。\n"
                "2. 判决仅依据上述黑名单语义评估：JD未触犯黑名单即判决合格 approved: true，无需JD与任何白名单或兴趣方向词相关联。\n"
                '3. 严格输出标准 JSON 格式：{"approved": true(合格保留)或false(命中黑名单淘汰), "reason": "50字以内的判定简述，明确写【合格保留】或【淘汰：具体原因】，严禁使用具有中英二义性的 pass 词汇"}。'
            )

        user_prompt = (
            f"职位名称: {card_title}\n"
            f"招聘公司: {company_name}\n"
            f"岗位描述(JD):\n{jd_text}\n\n"
            '请严格输出 JSON: {"approved": true(合格保留)/false(命中黑名单淘汰), "reason": "判定简述(请明确标注【合格保留】或【淘汰：原因】，严禁使用 pass)"}'
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        client = self.llm_client
        if not client:
            from .llm_config import create_llm_client

            client = self.llm_client = create_llm_client()

        try:
            res = client.chat_completion_json(messages)
            reason = str(res.get("reason", "精筛完成")).strip()
            passed = _coerce_screening_verdict(res, reason)
            return passed, reason
        except Exception as e:
            return True, f"LLM精筛调用异常，降级放行: {e}"

    @traceable(name="CandidateScreener.retest_with_critique", run_type="chain")
    def retest_with_critique(
        self,
        job: JobPosting,
        critique: str,
        current_prompt: str | None = None,
        policy: ScreeningPolicy | dict[str, Any] | None = None,
    ) -> ScreeningVerdict:
        """Re-evaluate a job description under human critique / feedback (Spec #340).

        Never fabricates: if the LLM call fails or returns empty data, raises
        RuntimeError so the caller can surface the error honestly (ADR 0010).
        """
        resolved_policy = _resolve_policy(policy)
        blacklist = sorted(
            {
                b.strip()
                for b in resolved_policy.jd_blacklist + resolved_policy.title_blacklist
                if b and b.strip()
            }
        )

        prompt_template = current_prompt or self.screening_prompt
        if prompt_template is None:
            from .screening_prompt import load_screening_prompt

            try:
                prompt_template = load_screening_prompt()
            except FileNotFoundError:
                prompt_template = ""

        prompt_prefix = f"{prompt_template}\n\n" if prompt_template else ""
        system_prompt = (
            f"{prompt_prefix}"
            "【筛选准则】：\n"
            f"- 当前配置的黑名单关键词(语义一票否决): {blacklist}\n\n"
            "【用户纠偏与复核模式】：\n"
            "用户对先前的初筛判定提出了批注反馈。请仔细阅读岗位JD、黑名单关键词以及用户的纠偏批注，"
            "重新严格依据上述铁律（特别是绝对禁止臆造黑名单外淘汰条件、复合技术工种正常落地偏向不予淘汰、黑名单次要提及豁免），"
            "对该岗位做出客观公正的最终裁决。\n\n"
            "【输出格式硬性约定】：\n"
            '严格输出标准 JSON 格式：{"approved": true(合格保留)或false(命中黑名单淘汰), "reason": "50字以内的判定简述，明确写【合格保留】或【淘汰：具体原因】，严禁使用具有中英二义性的 pass 词汇"}。'
        )

        user_prompt = (
            f"职位名称: {job.title}\n"
            f"招聘公司: {job.company_name}\n"
            f"岗位描述(JD):\n{job.job_description}\n\n"
            f"用户纠偏反馈/批注:\n{critique.strip()}\n\n"
            '请结合用户批注重新裁决并输出 JSON：{"approved": true(合格保留)/false(命中黑名单淘汰), "reason": "判定简述(明确标注【合格保留】或【淘汰：原因】，严禁使用 pass)"}'
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        client = self.llm_client
        if not client:
            from .llm_config import create_llm_client

            client = self.llm_client = create_llm_client()

        try:
            res = client.chat_completion_json(messages)
            if not isinstance(res, dict):
                raise ValueError("LLM 返回非字典响应")
            reason = str(res.get("reason", "重测完成")).strip()
            approved = _coerce_screening_verdict(res, reason)
            stage = "passed" if approved else "filtered_by_deep_screener"
            return ScreeningVerdict(approved=approved, reason=reason, stage=stage)
        except Exception as e:
            import sys

            sys.stderr.write(f"❌ LLM screening critique retest error: {e}\n")
            sys.stderr.flush()
            raise RuntimeError(f"LLM 纠偏重测失败，无法裁决：{e}") from e

    @traceable(name="CandidateScreener.evaluate_jd", run_type="chain")
    def evaluate_jd(
        self,
        job: JobPosting,
        current_prompt: str | None = None,
        policy: ScreeningPolicy | dict[str, Any] | None = None,
    ) -> ScreeningVerdict:
        """Objectively evaluate a job description against living prompt and policy (Spec #346, Issue #347).

        Unlike ``retest_with_critique``, this evaluation carries zero critique bias.
        Raises RuntimeError on LLM failure (ADR 0010: never fabricate output).
        """
        jd = (job.job_description or "").strip()
        if not _jd_is_substantive(jd):
            reason = UNUSABLE_JD_REASON.format(length=len(jd))
            return ScreeningVerdict(
                approved=False,
                reason=reason,
                stage="jd_unavailable",
            )

        resolved = _resolve_policy(policy)
        if not resolved.enable_screening:
            return ScreeningVerdict(
                approved=True,
                reason="筛选策略未启用",
                stage="passed",
            )

        blacklist = sorted(
            {b.strip() for b in resolved.jd_blacklist + resolved.title_blacklist if b and b.strip()}
        )
        if not blacklist:
            return ScreeningVerdict(
                approved=True,
                reason="未配置黑名单，JD语义精筛默认放行（白名单对JD正文零否决权）",
                stage="passed",
            )

        prompt_template = current_prompt or self.screening_prompt
        if prompt_template is None:
            from .screening_prompt import load_screening_prompt

            try:
                prompt_template = load_screening_prompt()
            except FileNotFoundError:
                prompt_template = ""

        prompt_prefix = f"{prompt_template}\n\n" if prompt_template else ""
        system_prompt = (
            f"{prompt_prefix}"
            "【筛选准则】：\n"
            f"- 当前配置的黑名单关键词(语义一票否决): {blacklist}\n\n"
            "【客观标准精筛模式】：\n"
            "请仔细阅读岗位JD与黑名单关键词，严格依据上述精筛铁律与原则（特别是绝对禁止臆造黑名单外淘汰条件、复合技术工种正常落地偏向不予淘汰、黑名单次要提及豁免），"
            "对该岗位做出客观公正的判决。\n\n"
            "【输出格式硬性约定】：\n"
            '严格输出标准 JSON 格式：{"approved": true(合格保留)或false(命中黑名单淘汰), "reason": "50字以内的判定简述，明确写【合格保留】或【淘汰：具体原因】，严禁使用具有中英二义性的 pass 词汇"}。'
        )

        user_prompt = (
            f"职位名称: {job.title}\n"
            f"招聘公司: {job.company_name}\n"
            f"岗位描述(JD):\n{jd}\n\n"
            '请客观裁决并输出 JSON：{"approved": true(合格保留)/false(命中黑名单淘汰), "reason": "判定简述(明确标注【合格保留】或【淘汰：原因】，严禁使用 pass)"}'
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        client = self.llm_client
        if not client:
            from .llm_config import create_llm_client

            client = self.llm_client = create_llm_client()

        try:
            res = client.chat_completion_json(messages)
            if not isinstance(res, dict):
                raise ValueError("LLM 返回非字典响应")
            reason = str(res.get("reason", "精筛完成")).strip()
            approved = _coerce_screening_verdict(res, reason)
            stage = "passed" if approved else "filtered_by_deep_screener"
            return ScreeningVerdict(approved=approved, reason=reason, stage=stage)
        except Exception as e:
            import sys

            sys.stderr.write(f"❌ LLM screening evaluation error: {e}\n")
            sys.stderr.flush()
            raise RuntimeError(f"LLM 客观精筛评估失败，无法裁决：{e}") from e

    _evaluate_jd = evaluate_jd


    @traceable(name="CandidateScreener.refine_screening_prompt", run_type="chain")
    def refine_screening_prompt(
        self,
        job: JobPosting,
        critique: str,
        original_verdict: str | dict[str, Any] | None = None,
        revised_verdict: str | dict[str, Any] | None = None,
        current_prompt: str | None = None,
    ) -> str:
        """Rewrite the entire Screening Prompt in light of one concrete critique example.

        The editable document stays coherent by whole-document rewrite: every
        still-valid point must be preserved, the new lesson is generalized into
        the prose. Never fabricates — failures raise so the caller can surface
        a real error (ADR 0010, Spec #340).
        """
        if current_prompt is None:
            from .screening_prompt import load_screening_prompt

            current_prompt = load_screening_prompt()

        system_prompt = (
            "你是一名资深的岗位精筛智能体提示词打磨专家 (Screening Prompt Refinement Specialist)。\n"
            "下面给出当前求职者配置的《精筛长期记忆提示词》(Screening Prompt)，以及一次具体的岗位精筛纠偏实例：初次判定 →（求职者纠偏反馈）→ 重测纠偏后的裁决结果。\n"
            "你的任务：结合该实例反思，整篇重写这份 Screening Prompt，使其成为更准确、更通用、严谨客观且连贯可读的最终精筛准则。\n\n"
            "【改写规则】：\n"
            "1. 【保留全部既有条款 — 最重要】：必须完整保留当前提示词中所有仍然有效的精筛铁律与要点（尤其是绝对禁止臆造黑名单外淘汰条件、复合技术工种正常落地偏向不予淘汰等核心铁律），严禁在重写中丢失历史沉淀；只做修正、补充、去重与泛化。\n"
            "2. 【通用化与避免过拟合】：把本次实例中的纠偏经验抽象提炼为适用于同类技术岗位和同类场景的通用判定原则，严禁绑定具体公司名称、具体职位标题或仅针对单个 JD 特例。\n"
            "3. 【理解并转化批注意图】：求职者的反馈可能口语化、零散，必须深刻理解其意图并转化为清晰可执行的判定指令，而不是逐字生硬照搬。\n"
            "4. 【保持既有 Markdown 结构】：延续当前文档的结构与【精筛判定铁律与原则】编号条目风格，输出仍然是一份可直接使用的完整提示词。\n"
            '5. 【严格 JSON 输出】：{"prompt": "改进后的完整提示词全文"}'
        )

        orig_str = str(original_verdict or "淘汰 (初筛)")
        rev_str = str(revised_verdict or "合格保留 (重测)")
        jd_snippet = (job.job_description or "")[:350]

        user_content = (
            f"【当前 Screening Prompt 全文】：\n{current_prompt}\n\n"
            f"目标职位: {job.title}\n"
            f"目标公司: {job.company_name}\n"
            f"岗位描述片段:\n{jd_snippet}\n\n"
            f"【本次纠偏实例】\n"
            f"初次判定: {orig_str}\n\n"
            f"求职者纠偏反馈:\n{critique or '用户手动纠偏'}\n\n"
            f"重测后裁决: {rev_str}\n\n"
            '请结合上述实例整篇重写 Screening Prompt，严格输出 JSON：{"prompt": "改进后的完整提示词全文"}'
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        client = self.llm_client
        if not client:
            from .llm_config import create_llm_client

            client = self.llm_client = create_llm_client()

        try:
            data = client.chat_completion_json(messages)
            prompt_text = str(data.get("prompt") or "").strip()
            if not prompt_text:
                raise ValueError("LLM 返回空 prompt 字段")
            return prompt_text
        except Exception as e:
            import sys

            sys.stderr.write(f"❌ Screening Prompt refinement error: {e}\n")
            sys.stderr.flush()
            raise RuntimeError(f"提示词打磨失败，无法生成改进版 Screening Prompt：{e}") from e

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
