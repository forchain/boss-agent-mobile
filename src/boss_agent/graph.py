"""
boss_agent.graph
================
LangGraph multi-agent workflow for multi-tier job screening, JD evaluation, and greeting generation.
"""

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .models import ScreeningPolicy
from .pages import JobCardBrief


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

    def __init__(self, llm_client: Any | None = None) -> None:
        from .matching import JobMatchGreetingService

        self.matching_service = JobMatchGreetingService(llm_client=llm_client)

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


def build_job_application_graph(llm_client: Any | None = None) -> Any:
    """Construct and compile the stateful job screening and application graph."""
    screener_agent = JDSemanticScreenerAgent(llm_client=llm_client)
    drafter_agent = GreetingDrafterAgent(llm_client=llm_client)

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


def run_job_application_graph(
    card: JobCardBrief | dict[str, Any],
    policy: ScreeningPolicy | dict[str, Any] | None = None,
    candidate_profile: Any | None = None,
    jd_text: str = "",
    llm_client: Any | None = None,
    graph: Any | None = None,
) -> JobApplicationState:
    """Run the job application workflow graph on a single job posting card."""
    if graph is None:
        graph = build_job_application_graph(llm_client=llm_client)

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

    result = graph.invoke(initial_state)
    return result
