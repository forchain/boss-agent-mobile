"""
boss_agent.graph
================
LangGraph multi-agent workflow for multi-tier job screening, JD evaluation, and greeting generation.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import traceable

from .models import ScreeningPolicy
from .pages import JobCardBrief

logger = logging.getLogger(__name__)


class JobApplicationState(TypedDict, total=False):
    """Execution state for single-job screening and outreach pipeline."""

    # Input specifications
    card: dict[str, Any]  # Serialized JobCardBrief
    screening_policy: dict[str, Any]  # Serialized ScreeningPolicy
    candidate_profile: dict[str, Any]  # Serialized StructuredCandidateProfile
    jd_text: str

    # Intermediate / Output: Keyword Screener
    keyword_pass: bool
    keyword_reason: str

    # Intermediate / Output: JD Semantic Screener (added in Ticket 2)
    deep_screen_pass: bool
    deep_screen_reason: str

    # Intermediate / Output: Greeting Drafter (added in Ticket 3)
    greeting_message: str
    match_score: int
    match_reasons: list[str]

    # Global status & execution audit
    status: str  # "pending", "filtered_by_keyword", "keyword_passed", "filtered_by_deep_screen", "greeting_drafted", "applied", "error"
    error_message: str


class JDSemanticScreenerAgent:
    """Token-optimized LLM agent evaluating JD against blacklist/whitelist constraints without resume."""

    def __init__(self, llm_client: Any | None = None) -> None:
        self.llm_client = llm_client

    @traceable(name="JDSemanticScreenerAgent.evaluate", run_type="chain")
    def evaluate(
        self,
        jd_text: str,
        card_title: str = "",
        company_name: str = "",
        policy: ScreeningPolicy | None = None,
    ) -> tuple[bool, str]:
        """Evaluate JD text against screening policy without loading candidate resume.

        Returns: (passed: bool, reason: str).
        """
        if not policy or not policy.enable_screening:
            return True, "筛选策略未启用"

        if not jd_text or not jd_text.strip():
            return True, "无详细JD文本，跳过语义精筛"

        blacklist = list(set(policy.jd_blacklist + policy.title_blacklist))
        whitelist = list(set(policy.title_whitelist))

        if not blacklist and not whitelist:
            return True, "未配置黑白名单，默认精筛通过"

        system_prompt = (
            "你是一名严谨的岗位精筛助手。你的唯一任务是依据【筛选准则】，深度阅读招聘岗位详情(JD)，"
            "判断该岗位是否应当被淘汰。\n"
            "【筛选准则】：\n"
            f"- 黑名单关键词(一票否决): {blacklist if blacklist else '无'}\n"
            f"- 白名单目标关键词: {whitelist if whitelist else '无'}\n\n"
            "【判决规则】：\n"
            "1. 若JD正文中明确要求黑名单中的技术栈、工作内容或岗位性质(如明确要求Java开发、微服务架构、销售外包或驻场等)，"
            "无论岗位标题如何，必须判决 pass: false。\n"
            "2. 若配置了白名单且JD正文与目标方向完全无关(挂羊头卖狗肉)，判决 pass: false。\n"
            "3. 岗位未触犯黑名单且核心工作内容符合方向时，判决 pass: true。\n"
            "4. 严格输出标准 JSON 格式：{\"pass\": true或false, \"reason\": \"50字以内的判定简述\"}。"
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

        if not self.llm_client:
            from droid_agent_core.llm import OpenAIChatClient

            self.llm_client = OpenAIChatClient()

        try:
            res = self.llm_client.chat_completion_json(messages)
            passed = bool(res.get("pass", True))
            reason = str(res.get("reason", "精筛完成"))
            return passed, reason
        except Exception as e:
            return True, f"LLM精筛调用异常，降级放行: {e}"


@traceable(name="keyword_screener_node", run_type="tool")
def keyword_screener_node(state: JobApplicationState) -> dict[str, Any]:
    """Purely deterministic keyword screener node evaluating job card against policy."""
    card_dict = state.get("card") or {}
    policy_dict = state.get("screening_policy") or {}
    policy = ScreeningPolicy.from_dict(policy_dict)

    title = card_dict.get("title", "")
    company = card_dict.get("company_name", "")
    tags = card_dict.get("tags") or []

    passed, reason = policy.matches_card_keywords(
        title=title,
        company_name=company,
        tags=tags,
    )

    return {
        "keyword_pass": passed,
        "keyword_reason": reason,
        "status": "keyword_passed" if passed else "filtered_by_keyword",
    }


def make_jd_semantic_screener_node(agent: JDSemanticScreenerAgent):
    """Factory creating the JD semantic screening node bound to an agent instance."""

    def jd_semantic_screener_node(state: JobApplicationState) -> dict[str, Any]:
        card = state.get("card") or {}
        policy_dict = state.get("screening_policy") or {}
        policy = ScreeningPolicy.from_dict(policy_dict)
        jd_text = state.get("jd_text") or ""
        title = card.get("title", "")
        company = card.get("company_name", "")

        passed, reason = agent.evaluate(
            jd_text=jd_text,
            card_title=title,
            company_name=company,
            policy=policy,
        )

        return {
            "deep_screen_pass": passed,
            "deep_screen_reason": reason,
            "status": "deep_screen_passed" if passed else "filtered_by_deep_screener",
        }

    return jd_semantic_screener_node


def should_continue_after_keyword(state: JobApplicationState) -> str:
    """Conditional edge router after keyword screener."""
    if state.get("keyword_pass", False):
        return "continue"
    return "end"


def should_continue_after_deep_screen(state: JobApplicationState) -> str:
    """Conditional edge router after JD semantic screener."""
    if state.get("deep_screen_pass", False):
        return "continue"
    return "end"


class GreetingDrafterAgent:
    """Agent generating personalized, anti-template greeting messages fusing full JD and candidate profile."""

    def __init__(
        self, llm_client: Any | None = None, matching_service: Any | None = None
    ) -> None:
        if matching_service is not None:
            self.matching_service = matching_service
        else:
            from .matching import JobMatchGreetingService

            self.matching_service = JobMatchGreetingService(llm_client=llm_client)

    @traceable(name="GreetingDrafterAgent.draft", run_type="chain")
    def draft(
        self,
        card: dict[str, Any],
        jd_text: str,
        candidate_profile: Any | None = None,
    ) -> Any:
        from .memory import StructuredCandidateProfile
        from .models import JobPosting

        posting = JobPosting(
            title=card.get("title", ""),
            company_name=card.get("company_name", ""),
            salary_range=card.get("salary_range", ""),
            job_description=jd_text,
            location=card.get("location"),
            tags=card.get("tags") or [],
            recruiter_name=card.get("recruiter_name"),
        )

        profile_obj: StructuredCandidateProfile | None = None
        if isinstance(candidate_profile, StructuredCandidateProfile):
            profile_obj = candidate_profile
        elif isinstance(candidate_profile, dict) and candidate_profile:
            profile_obj = StructuredCandidateProfile.from_dict(candidate_profile)

        return self.matching_service.evaluate_and_draft_greeting(job=posting, profile=profile_obj)


def make_greeting_drafter_node(agent: GreetingDrafterAgent):
    """Factory creating the greeting drafter node bound to an agent instance."""

    def greeting_drafter_node(state: JobApplicationState) -> dict[str, Any]:
        card = state.get("card") or {}
        jd_text = state.get("jd_text") or ""
        profile_dict = state.get("candidate_profile") or {}

        match_res = agent.draft(
            card=card,
            jd_text=jd_text,
            candidate_profile=profile_dict,
        )

        return {
            "greeting_message": match_res.greeting_message,
            "match_score": match_res.match_score,
            "match_reasons": match_res.match_reasons,
            "status": "greeting_drafted",
        }

    return greeting_drafter_node


def build_job_application_graph(
    llm_client: Any | None = None,
    matching_service: Any | None = None,
) -> Any:
    """Construct and compile the stateful job screening and application graph."""
    screener_agent = JDSemanticScreenerAgent(llm_client=llm_client)
    drafter_agent = GreetingDrafterAgent(llm_client=llm_client, matching_service=matching_service)

    builder = StateGraph(JobApplicationState)

    # 1. Register nodes
    builder.add_node("keyword_screener", keyword_screener_node)
    builder.add_node(
        "jd_semantic_screener",
        make_jd_semantic_screener_node(screener_agent),
    )
    builder.add_node(
        "greeting_drafter",
        make_greeting_drafter_node(drafter_agent),
    )

    # 2. Edges
    builder.add_edge(START, "keyword_screener")

    # If keyword screener passes, advance to jd_semantic_screener; otherwise terminate at END
    builder.add_conditional_edges(
        "keyword_screener",
        should_continue_after_keyword,
        {
            "continue": "jd_semantic_screener",
            "end": END,
        },
    )

    # If semantic screener passes, advance to greeting_drafter; otherwise terminate at END
    builder.add_conditional_edges(
        "jd_semantic_screener",
        should_continue_after_deep_screen,
        {
            "continue": "greeting_drafter",
            "end": END,
        },
    )

    # From greeting_drafter to END (ready for future Human Gate Checkpoint)
    builder.add_edge("greeting_drafter", END)

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
            "salary_range": card.salary_range,
            "location": card.location,
            "tags": card.tags,
            "snippet": card.snippet,
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

