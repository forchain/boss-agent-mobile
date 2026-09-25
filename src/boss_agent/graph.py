"""
boss_agent.graph
================
LangGraph workflows.

The job screening workflow is a thin traced adapter over the deep ``CandidateScreener``
module (ADR 0013); the resume lifecycle workflow below it remains self-contained.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import traceable

from .models import JobCardBrief, ScreeningPolicy
from .screening import CARD_PASS_REASON, CandidateScreener, CardVerdictStage

logger = logging.getLogger(__name__)


class JobApplicationState(TypedDict, total=False):
    """Execution state for single-job screening and outreach pipeline."""

    # Input specifications
    card: dict[str, Any]  # Serialized JobCardBrief
    screening_policy: dict[str, Any]  # Serialized ScreeningPolicy
    candidate_profile: dict[str, Any]  # Serialized StructuredCandidateProfile
    jd_text: str

    # Intermediate / Output: Card Screener (keywords, App-Enforced Filters, relaxation)
    card_pass: bool
    keyword_pass: bool
    keyword_reason: str
    app_rule_pass: bool
    app_rule_violation: str
    relaxed_by_whitelist: bool
    relaxation_reason: str

    # Intermediate / Output: JD evaluation and greeting drafting
    deep_screen_pass: bool
    deep_screen_reason: str
    greeting_message: str
    match_score: int
    match_reasons: list[str]
    jd_key_requirements: list[str]

    # Global status & execution audit
    status: str  # "pending", "keyword_passed", "filtered_by_keyword", "filtered_by_app_rule", "relaxed_by_whitelist", "greeting_drafted", "jd_unavailable", "error"
    error_message: str


def make_card_screener_node(screener: CandidateScreener):
    """Factory creating the card screening node bound to a screener instance.

    One node owns the whole card-level verdict: keyword matching, App-Enforced Filters
    and Whitelist Relaxation used to be three nodes plus a router, which is what let
    callers re-implement the same rules slightly differently (ADR 0013).
    """

    def card_screener_node(state: JobApplicationState) -> dict[str, Any]:
        policy = ScreeningPolicy.from_dict(state.get("screening_policy") or {})
        verdict = screener.evaluate_card(state.get("card") or {}, policy)
        rejected_by_keywords = verdict.stage is CardVerdictStage.FILTERED_BY_KEYWORD

        return {
            "card_pass": verdict.passed,
            "keyword_pass": not rejected_by_keywords,
            "keyword_reason": verdict.reason if rejected_by_keywords else CARD_PASS_REASON,
            "app_rule_pass": verdict.app_rule_pass,
            "app_rule_violation": verdict.app_rule_violation,
            "relaxed_by_whitelist": verdict.relaxed_by_whitelist,
            "relaxation_reason": verdict.relaxation_reason,
            "status": verdict.stage.value,
        }

    return card_screener_node


def make_job_evaluation_node(screener: CandidateScreener):
    """Factory creating the full-JD evaluation node bound to a screener instance."""

    def job_evaluation_node(state: JobApplicationState) -> dict[str, Any]:
        policy = ScreeningPolicy.from_dict(state.get("screening_policy") or {})
        result = screener.evaluate_job(
            card=state.get("card") or {},
            jd_text=state.get("jd_text") or "",
            profile=state.get("candidate_profile") or None,
            policy=policy,
        )
        return {
            "deep_screen_pass": result.passed,
            "deep_screen_reason": result.reason,
            "greeting_message": result.greeting_message,
            "match_score": result.match_score,
            "match_reasons": result.match_reasons,
            "jd_key_requirements": result.jd_key_requirements,
            "status": "greeting_drafted" if result.passed else result.stage.value,
            "error_message": result.error_message,
        }

    return job_evaluation_node


def should_continue_after_card_screening(state: JobApplicationState) -> str:
    """Conditional edge router after the card screening node."""
    if state.get("card_pass", False):
        return "continue"
    return "end"


def build_job_application_graph(
    llm_client: Any | None = None,
    matching_service: Any | None = None,
    screener: CandidateScreener | None = None,
) -> Any:
    """Construct and compile the stateful job screening workflow.

    The graph is a thin traced adapter over ``CandidateScreener`` (ADR 0013): it keeps
    the screening stages observable as a LangGraph run with LangSmith tags while every
    screening rule itself lives in the screener module.
    """
    resolved_screener = screener or CandidateScreener(
        llm_client=llm_client, matching_service=matching_service
    )

    builder = StateGraph(JobApplicationState)
    builder.add_node("card_screener", make_card_screener_node(resolved_screener))
    builder.add_node("job_evaluation", make_job_evaluation_node(resolved_screener))

    builder.add_edge(START, "card_screener")
    # Only a card that survived card screening with a usable verdict earns a JD evaluation;
    # a rejected card terminates the workflow without spending a single token.
    builder.add_conditional_edges(
        "card_screener",
        should_continue_after_card_screening,
        {
            "continue": "job_evaluation",
            "end": END,
        },
    )
    builder.add_edge("job_evaluation", END)

    return builder.compile()


@traceable(name="run_job_application_graph", run_type="chain")
def run_job_application_graph(
    card: JobCardBrief | dict[str, Any],
    policy: ScreeningPolicy | dict[str, Any] | None = None,
    candidate_profile: Any | None = None,
    jd_text: str = "",
    llm_client: Any | None = None,
    matching_service: Any | None = None,
    graph: Any | None = None,
    config: dict[str, Any] | None = None,
) -> JobApplicationState:
    """Run the job application workflow graph on a single job posting card."""
    if graph is None:
        graph = build_job_application_graph(
            llm_client=llm_client, matching_service=matching_service
        )

    if isinstance(card, JobCardBrief):
        card_dict = {
            "title": card.title,
            "company_name": card.company_name,
            "recruiter_name": card.recruiter_name,
            "recruiter_title": card.recruiter_title,
            "salary_range": card.salary_range,
            "location": card.location,
            "tags": card.tags,
            "digest": card.digest or card.snippet,
            "snippet": card.snippet or card.digest,
            "is_headhunter": card.is_headhunter,
        }
    else:
        card_dict = dict(card)

    if policy is None:
        policy_dict = ScreeningPolicy().to_dict()
    elif isinstance(policy, ScreeningPolicy):
        policy_dict = policy.to_dict()
    else:
        policy_dict = dict(policy)

    if candidate_profile is None:
        profile_dict = {}
    elif hasattr(candidate_profile, "to_dict"):
        profile_dict = candidate_profile.to_dict()
    else:
        profile_dict = dict(candidate_profile)

    initial_state: JobApplicationState = {
        "card": card_dict,
        "screening_policy": policy_dict,
        "candidate_profile": profile_dict,
        "jd_text": jd_text,
        "status": "pending",
    }

    run_config: dict[str, Any] = {
        "tags": ["boss-agent", "screening-graph"],
        "metadata": {
            "title": card_dict.get("title", ""),
            "company_name": card_dict.get("company_name", ""),
            "salary_range": card_dict.get("salary_range", ""),
            "location": card_dict.get("location", ""),
        },
    }
    if config:
        run_config.update(config)

    result = graph.invoke(initial_state, config=run_config)
    return result


# ==============================================================================
# Resume Lifecycle Graph (Stateful Ingestion, Structuring, Diffing, Persistence)
# ==============================================================================


class ResumeLifecycleState(TypedDict, total=False):
    """Execution state for candidate resume ingestion, normalization, diffing, and persistence."""

    file_path: str
    file_name: str
    user_id: str
    raw_resume_text: str
    extracted_metadata: dict[str, Any]
    profile_document: str
    normalized_profile: dict[str, Any]
    existing_profile: dict[str, Any] | None
    diff_summary: str
    merge_mode: str  # "initial" | "merge" | "overwrite"
    await_review: bool  # if True and merge_mode is None, pause after diff_analyzer
    final_profile: dict[str, Any]
    revision_record: dict[str, Any]
    status: str  # "text_extracted" | "document_generated" | "normalized" | "diff_ready" | "completed" | "error"
    error_message: str | None


def resume_text_extractor_node(state: ResumeLifecycleState) -> dict[str, Any]:
    """Extract raw ground-truth text from resume file if not already provided."""
    file_path = state.get("file_path", "")
    raw_text = state.get("raw_resume_text", "")
    if not raw_text and file_path:
        from .memory import ResumeTextExtractor

        extractor = ResumeTextExtractor()
        raw_text = extractor.extract_text(file_path)

    from pathlib import Path

    file_name = state.get("file_name") or (Path(file_path).name if file_path else "resume.txt")
    return {
        "raw_resume_text": raw_text,
        "file_name": file_name,
        "status": "text_extracted",
    }


def make_resume_document_generator_node(llm_client: Any | None = None):
    """Factory creating LLM profile document generator node."""

    def resume_document_generator_node(state: ResumeLifecycleState) -> dict[str, Any]:
        raw_text = state.get("raw_resume_text", "")
        if not raw_text.strip():
            return {
                "status": "error",
                "error_message": "Empty resume text content.",
            }

        client = llm_client
        if not client:
            from droid_agent_core.llm import OpenAIChatClient

            client = OpenAIChatClient()

        prompt = (
            "请全面、深度、无损地解析以下求职者原始简历文本，并以标准严格的 JSON 格式输出：\n\n"
            f"[简历文本内容]\n{raw_text}\n\n"
            "输出的 JSON 结构规范如下：\n"
            "{\n"
            '  "name": "姓名",\n'
            '  "years_of_experience": 经验年限(整数),\n'
            '  "target_positions": ["期望职位1", "期望职位2"],\n'
            '  "core_skills": ["分类1: 技能列表", "分类2: 技能列表"],\n'
            '  "profile_document": "详尽完整的 Markdown 格式候选人全景画像文档",\n'
            '  "work_experiences": [],\n'
            '  "projects": []\n'
            "}\n\n"
            "【profile_document 的 Markdown 章节规范（必须极其详尽，100%保留所有项目、架构、技术栈、量化成果与开源链接，严禁做删减概括）】：\n"
            "# 候选人全景画像 (Candidate Profile)\n\n"
            "## 1. 核心职业定位与背景概览\n"
            "(包含姓名、经验年限、学历背景、求职意向与核心定位优势)\n\n"
            "## 2. 核心技术栈与专业能力矩阵\n"
            "(按维度清晰列出 AI Agent/大模型编排、编程语言、后端架构、前端全栈、Web3/量化、云原生与工程化等)\n\n"
            "## 3. 核心主导项目与技术攻坚 (Key Projects & Architecture)\n"
            "(逐个详述各项目/经历的业务背景、系统架构设计、负责模块、攻坚难点、量化表现与开源代码仓库链接)\n\n"
            "## 4. 可量化成果与标志性突破 (Measurable Achievements)\n"
            "(量化指标突破、业务跃升、日活表现、性能突破、行业或社区影响力)\n\n"
            "## 5. 资格认证、语言能力与附加信息\n\n"
            "注意：必须输出标准严格合法的 JSON。所有经历与项目必须在 profile_document 中全量无损展开；字符串内严禁未转义的双引号（若需引用请用中文书名号《》或单引号）。"
        )
        messages = [
            {
                "role": "system",
                "content": "You are a professional HR assistant specializing in parsing candidate resumes into structured JSON with 100% fidelity. Always output valid, complete JSON.",
            },
            {"role": "user", "content": prompt},
        ]

        extra_payload = None
        cfg = getattr(client, "config", None)
        if cfg and (
            "minimax" in getattr(cfg, "base_url", "").lower()
            or "minimax" in getattr(cfg, "model", "").lower()
        ):
            extra_payload = {"thinking": {"type": "disabled"}}

        try:
            res = client.chat_completion_json(
                messages, max_tokens=16384, extra_payload=extra_payload
            )
        except Exception:
            res = {"raw_summary": raw_text[:500]}

        return {
            "extracted_metadata": res,
            "profile_document": res.get("profile_document") or res.get("raw_summary") or "",
            "status": "document_generated",
        }

    return resume_document_generator_node


def resume_normalizer_node(state: ResumeLifecycleState) -> dict[str, Any]:
    """Self-healing node ensuring critical fields, valid arrays, and fallback markdown exist."""
    from .memory import ProfileNormalizer, StructuredCandidateProfile

    raw_text = state.get("raw_resume_text", "")
    extracted = dict(state.get("extracted_metadata") or {})
    if not extracted.get("profile_document") and state.get("profile_document"):
        extracted["profile_document"] = state["profile_document"]

    normalized = ProfileNormalizer.normalize(extracted, raw_text=raw_text)
    profile_obj = StructuredCandidateProfile.from_dict(normalized)
    return {
        "normalized_profile": profile_obj.to_dict(),
        "profile_document": profile_obj.profile_document,
        "status": "normalized",
    }


def make_resume_diff_analyzer_node(broker: Any | None = None):
    """Factory creating semantic diff analyzer against existing active profile."""

    def resume_diff_analyzer_node(state: ResumeLifecycleState) -> dict[str, Any]:
        user_id = state.get("user_id") or "default"
        nonlocal broker
        if not broker:
            from .broker import PocketBaseBroker

            broker = PocketBaseBroker()

        import asyncio

        try:
            asyncio.get_running_loop()
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                existing = pool.submit(
                    asyncio.run, broker.get_candidate_profile(user_id=user_id)
                ).result(timeout=3.0)
        except Exception:
            try:
                existing = asyncio.run(broker.get_candidate_profile(user_id=user_id))
            except Exception:
                existing = None

        new_prof = state.get("normalized_profile") or {}
        if not existing or (not existing.get("name") and not existing.get("raw_summary")):
            return {
                "existing_profile": None,
                "diff_summary": "【初次录入】未发现历史画像记录，已自动完成全量初始化解析。",
                "merge_mode": state.get("merge_mode") or "initial",
                "status": "diff_ready",
            }

        # Compare old vs new
        diffs = []
        old_name = existing.get("name", "")
        new_name = new_prof.get("name", "")
        if new_name and new_name != old_name:
            diffs.append(f"- 姓名变更: {old_name or '(未设)'} -> {new_name}")

        old_exp = existing.get("years_of_experience", 0)
        new_exp = new_prof.get("years_of_experience", 0)
        if new_exp and new_exp != old_exp:
            diffs.append(f"- 经验年限: {old_exp}年 -> {new_exp}年")

        old_pos = set(existing.get("target_positions") or [])
        new_pos = set(new_prof.get("target_positions") or [])
        added_pos = new_pos - old_pos
        if added_pos:
            diffs.append(f"- 新增期望职位: {', '.join(added_pos)}")

        old_skills = set(existing.get("core_skills") or [])
        new_skills = set(new_prof.get("core_skills") or [])
        added_skills = new_skills - old_skills
        if added_skills:
            sample_added = list(added_skills)[:5]
            diffs.append(f"- 新增技能标签: {len(added_skills)} 项 ({', '.join(sample_added)})")

        diffs.append("- 全景画像文档: 最新解析版本已就绪，覆盖架构与项目细节")
        summary_text = "\n".join(diffs)

        return {
            "existing_profile": existing,
            "diff_summary": summary_text,
            "merge_mode": state.get("merge_mode") or "merge",
            "status": "diff_ready",
        }

    return resume_diff_analyzer_node


def make_resume_persister_node(broker: Any | None = None):
    """Factory creating persistence node saving to candidate_profiles and resume_revisions."""

    def resume_persister_node(state: ResumeLifecycleState) -> dict[str, Any]:
        user_id = state.get("user_id") or "default"
        nonlocal broker
        if not broker:
            from .broker import PocketBaseBroker

            broker = PocketBaseBroker()

        merge_mode = state.get("merge_mode") or "initial"
        incoming = state.get("normalized_profile") or {}
        existing = state.get("existing_profile")

        if merge_mode in ["overwrite", "initial"] or not existing:
            final = dict(incoming)
            final["work_experiences"] = final.get("work_experiences") or []
            final["projects"] = final.get("projects") or []
            final["project_highlights"] = final.get("project_highlights") or []
            final["education"] = final.get("education") or []
            final["core_skills"] = final.get("core_skills") or []
            final["target_positions"] = final.get("target_positions") or []
            incoming_doc = incoming.get("profile_document") or incoming.get("raw_summary") or ""
            final["profile_document"] = incoming_doc
            final["raw_summary"] = incoming_doc
        else:
            # Merge mode: union of skills and positions, keep latest incoming document
            final = dict(existing)
            if incoming.get("name"):
                final["name"] = incoming["name"]
            if incoming.get("years_of_experience"):
                final["years_of_experience"] = incoming["years_of_experience"]

            merged_skills = list(
                dict.fromkeys(
                    (existing.get("core_skills") or []) + (incoming.get("core_skills") or [])
                )
            )
            final["core_skills"] = merged_skills

            merged_pos = list(
                dict.fromkeys(
                    (existing.get("target_positions") or [])
                    + (incoming.get("target_positions") or [])
                )
            )
            final["target_positions"] = merged_pos

            incoming_doc = incoming.get("profile_document") or incoming.get("raw_summary") or ""
            final["profile_document"] = incoming_doc
            final["raw_summary"] = incoming_doc
            final["raw_resume_text"] = incoming.get("raw_resume_text") or existing.get(
                "raw_resume_text", ""
            )
            final["work_experiences"] = (
                incoming.get("work_experiences")
                or existing.get("work_experiences")
                or []
            )
            final["projects"] = (
                incoming.get("projects")
                or existing.get("projects")
                or []
            )
            final["project_highlights"] = (
                incoming.get("project_highlights")
                or existing.get("project_highlights")
                or []
            )
            final["education"] = (
                incoming.get("education")
                or existing.get("education")
                or []
            )

        async def _save():
            saved_prof = await broker.save_candidate_profile(final, user_id=user_id)
            f_name = state.get("file_name") or "resume.txt"
            f_type = Path(f_name).suffix.lstrip(".") or "txt"
            raw_text = state.get("raw_resume_text", "")
            rev_data = {
                "user_id": user_id,
                "file_name": f_name,
                "file_type": f_type,
                "file_size": len(raw_text.encode("utf-8")),
                "extracted_text": raw_text,
                "diff_summary": state.get("diff_summary", "画像更新"),
            }
            rev_rec = await broker.create_resume_revision(rev_data, user_id=user_id)
            return saved_prof, rev_rec

        try:
            try:
                asyncio.get_running_loop()
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    saved_prof, rev_rec = pool.submit(asyncio.run, _save()).result(timeout=10.0)
            except RuntimeError:
                saved_prof, rev_rec = asyncio.run(_save())
        except Exception as e:
            logger.warning("Resume persister save fallback: %s", e)
            saved_prof = final
            rev_rec = None

        return {
            "final_profile": saved_prof,
            "revision_record": rev_rec,
            "status": "completed",
        }

    return resume_persister_node


def should_continue_after_diff(state: ResumeLifecycleState) -> str:
    """Conditional edge router after diff analyzer."""
    if state.get("await_review", False):
        return "end"
    return "persist"


def build_resume_lifecycle_graph(
    llm_client: Any | None = None,
    broker: Any | None = None,
) -> Any:
    """Construct and compile the stateful candidate resume lifecycle graph."""
    builder = StateGraph(ResumeLifecycleState)

    # 1. Add nodes
    builder.add_node("text_extractor", resume_text_extractor_node)
    builder.add_node(
        "profile_document_generator",
        make_resume_document_generator_node(llm_client=llm_client),
    )
    builder.add_node("profile_normalizer", resume_normalizer_node)
    builder.add_node("diff_analyzer", make_resume_diff_analyzer_node(broker=broker))
    builder.add_node("profile_persister", make_resume_persister_node(broker=broker))

    # 2. Add edges
    builder.add_edge(START, "text_extractor")
    builder.add_edge("text_extractor", "profile_document_generator")
    builder.add_edge("profile_document_generator", "profile_normalizer")
    builder.add_edge("profile_normalizer", "diff_analyzer")
    builder.add_conditional_edges(
        "diff_analyzer",
        should_continue_after_diff,
        {
            "persist": "profile_persister",
            "end": END,
        },
    )
    builder.add_edge("profile_persister", END)

    return builder.compile()


def run_resume_lifecycle_graph(
    file_path: str = "",
    raw_resume_text: str = "",
    file_name: str = "",
    user_id: str = "default",
    merge_mode: str | None = None,
    await_review: bool = False,
    llm_client: Any | None = None,
    broker: Any | None = None,
    graph: Any | None = None,
) -> ResumeLifecycleState:
    """Run the resume lifecycle workflow graph."""
    if graph is None:
        graph = build_resume_lifecycle_graph(llm_client=llm_client, broker=broker)

    initial_state: ResumeLifecycleState = {
        "file_path": file_path,
        "raw_resume_text": raw_resume_text,
        "file_name": file_name,
        "user_id": user_id,
        "merge_mode": merge_mode or "",
        "await_review": await_review,
        "status": "pending",
    }

    result = graph.invoke(initial_state)
    return result

