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


def should_continue_after_keyword(state: JobApplicationState) -> str:
    """Conditional edge router after keyword screener."""
    if state.get("keyword_pass", False):
        return "continue"
    return "end"


def build_job_application_graph(llm_client: Any | None = None) -> Any:
    """Construct and compile the stateful job screening and application graph."""
    builder = StateGraph(JobApplicationState)

    # 1. Register nodes
    builder.add_node("keyword_screener", keyword_screener_node)

    # 2. Edges
    builder.add_edge(START, "keyword_screener")

    # In Ticket 1, 'continue' terminates at END with status 'keyword_passed'.
    # In Ticket 2, 'continue' will route to 'jd_semantic_screener'.
    builder.add_conditional_edges(
        "keyword_screener",
        should_continue_after_keyword,
        {
            "continue": END,
            "end": END,
        },
    )

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
