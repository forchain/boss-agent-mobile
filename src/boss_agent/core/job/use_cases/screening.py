"""
boss_agent.core.job.use_cases.screening
=======================================
Deep Candidate Screener use case (ADR 0013, Spec #231).

The verdicts and pure rules live in :mod:`boss_agent.core.job.domain.screening`; this
module is the apply-facing use case that owns the two decisions the pipeline calls:

    evaluate_card(card, policy)                        -> CardScreeningVerdict
    evaluate_job(card, jd_text, profile, policy, prompt) -> JobEvaluationResult

Both an LLM client and the prompt documents are injected by the caller at the edge;
nothing here reads configuration or creates a client mid-run.
"""

import logging
import sys
from typing import Any

from langsmith import traceable

from boss_agent.core.candidate.domain.candidate_profile import CandidateProfile
from boss_agent.core.job.domain.job_entities import JobCardBrief, JobPosting
from boss_agent.core.job.domain.screening import (
    CARD_PASS_REASON,
    UNUSABLE_JD_REASON,
    CardFacets,
    CardScreeningVerdict,
    CardVerdictStage,
    JobEvaluationResult,
    JobVerdictStage,
    ScreeningVerdict,
    _coerce_screening_verdict,
    _jd_is_substantive,
    _resolve_policy,
    _resolve_profile,
)
from boss_agent.core.job.domain.screening_policy import ScreeningPolicy
from boss_agent.core.job.use_cases.matching import JobMatchGreetingService

logger = logging.getLogger(__name__)

__all__ = [
    "CARD_PASS_REASON",
    "CandidateScreener",
    "CardFacets",
    "CardScreeningVerdict",
    "CardVerdictStage",
    "JobEvaluationResult",
    "JobVerdictStage",
    "ScreeningVerdict",
    "UNUSABLE_JD_REASON",
]


class CandidateScreener:
    """Deep module owning every candidate screening rule and greeting decision.

    Dependencies are injected so screening can be exercised over pure domain data
    (``JobCardBrief`` / ``ScreeningPolicy`` / ``CandidateProfile``) with a
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
        violates a soft App-Enforced Filter is only rejected if Whitelist Relaxation
        does not rescue it, and splitting them across callers is what let the two worker
        handlers drift apart. The hard/soft split is the one exception that must not be
        collapsed — see the hard-filter branch below.
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

        app_verdict = resolved.evaluate_app_enforced_filters_by_severity(
            is_headhunter=facets.is_headhunter,
            commute_distance_km=facets.commute_distance_km,
        )
        if app_verdict.passed:
            return CardScreeningVerdict.approved()

        # 硬性过滤（通勤距离超限）一票否决：距离是客观物理限制，不存在"兴趣足够强
        # 就值得通勤"的豁免，因此这里绝不进入白名单放宽路由（issue #437）。拒绝
        # 理由仍取合并串，以便同卡片上并存的软性违例不被审计丢弃；"硬性约束不可豁免"
        # 的标记已由 policy 拼进 hard_violation，卡片层与详情页层因此说同一句话。
        if app_verdict.hard_violation:
            return CardScreeningVerdict.rejected_by_app_rule(app_verdict.violation)

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
                app_rule_violation=app_verdict.soft_violation,
            )
        return CardScreeningVerdict.rejected_by_app_rule(app_verdict.soft_violation)

    # ------------------------------------------------------------------
    # Stage 2: full-JD evaluation and greeting drafting
    # ------------------------------------------------------------------
    @traceable(name="CandidateScreener.evaluate_job", run_type="chain")
    def evaluate_job(
        self,
        card: JobCardBrief | JobPosting | dict[str, Any],
        jd_text: str,
        profile: CandidateProfile | dict[str, Any] | None = None,
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

        client = self._llm_client()
        if client is None:
            return True, "未注入LLM客户端，JD语义精筛降级放行"

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

        client = self._llm_client()
        if client is None:
            raise RuntimeError("LLM 纠偏重测失败，无法裁决：未注入LLM客户端")

        try:
            res = client.chat_completion_json(messages)
            if not isinstance(res, dict):
                raise ValueError("LLM 返回非字典响应")
            reason = str(res.get("reason", "重测完成")).strip()
            approved = _coerce_screening_verdict(res, reason)
            stage = "passed" if approved else "filtered_by_deep_screener"
            return ScreeningVerdict(approved=approved, reason=reason, stage=stage)
        except Exception as e:
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

        client = self._llm_client()
        if client is None:
            raise RuntimeError("LLM 客观精筛评估失败，无法裁决：未注入LLM客户端")

        try:
            res = client.chat_completion_json(messages)
            if not isinstance(res, dict):
                raise ValueError("LLM 返回非字典响应")
            reason = str(res.get("reason", "精筛完成")).strip()
            approved = _coerce_screening_verdict(res, reason)
            stage = "passed" if approved else "filtered_by_deep_screener"
            return ScreeningVerdict(approved=approved, reason=reason, stage=stage)
        except Exception as e:
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
        current_prompt = current_prompt or self.screening_prompt or ""

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

        client = self._llm_client()
        if client is None:
            raise RuntimeError("提示词打磨失败，无法生成改进版 Screening Prompt：未注入LLM客户端")

        try:
            data = client.chat_completion_json(messages)
            prompt_text = str(data.get("prompt") or "").strip()
            if not prompt_text:
                raise ValueError("LLM 返回空 prompt 字段")
            return prompt_text
        except Exception as e:
            sys.stderr.write(f"❌ Screening Prompt refinement error: {e}\n")
            sys.stderr.flush()
            raise RuntimeError(f"提示词打磨失败，无法生成改进版 Screening Prompt：{e}") from e

    def _llm_client(self) -> Any | None:
        """The injected client, falling back to the matching service's own."""
        if self.llm_client is not None:
            return self.llm_client
        if self._matching_service is not None:
            return getattr(self._matching_service, "llm_client", None)
        return None

    def _greeting_service(self) -> JobMatchGreetingService:
        """Lazily build the greeting service so save-only runs never load LLM config."""
        if self._matching_service is None:
            self._matching_service = JobMatchGreetingService(
                llm_client=self._llm_client(), greeting_prompt=self.greeting_prompt
            )
        return self._matching_service
