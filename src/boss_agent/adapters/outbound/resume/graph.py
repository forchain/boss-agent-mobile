"""
boss_agent.adapters.outbound.resume.graph
=========================================
LangGraph workflows.

The module now holds the resume lifecycle workflow only: stateful ingestion,
normalization, diffing, and persistence for candidate resumes (ADR 0008). Card-level
and job-level screening is not graphed here — callers invoke ``CandidateScreener``
directly (ADR 0013), which is the seam every caller migrated to.
"""

import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from boss_agent.core.candidate.use_cases.resume_lifecycle import (
    analyze_profile_diff,
    extract_resume_text,
    generate_profile_document,
    normalize_extracted_profile,
    persist_resume_profile,
)

from .memory import ResumeTextExtractor

logger = logging.getLogger(__name__)


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
    return extract_resume_text(
        ResumeTextExtractor(),
        file_path=state.get("file_path", ""),
        raw_resume_text=state.get("raw_resume_text", ""),
        file_name=state.get("file_name") or "",
    )


def make_resume_document_generator_node(llm_client: Any | None = None):
    """Factory creating LLM profile document generator node."""

    def resume_document_generator_node(state: ResumeLifecycleState) -> dict[str, Any]:
        client = llm_client
        if not client:
            from boss_agent.adapters.outbound.llm.llm_config import create_llm_client

            client = create_llm_client()

        return generate_profile_document(client, state.get("raw_resume_text", ""))

    return resume_document_generator_node


def resume_normalizer_node(state: ResumeLifecycleState) -> dict[str, Any]:
    """Self-healing node ensuring critical fields, valid arrays, and fallback markdown exist."""
    return normalize_extracted_profile(
        state.get("extracted_metadata"),
        raw_resume_text=state.get("raw_resume_text", ""),
        profile_document=state.get("profile_document", ""),
    )


def make_resume_diff_analyzer_node(broker: Any | None = None):
    """Factory creating semantic diff analyzer against existing active profile."""

    def resume_diff_analyzer_node(state: ResumeLifecycleState) -> dict[str, Any]:
        user_id = state.get("user_id") or "default"
        nonlocal broker
        if not broker:
            from boss_agent.broker import PocketBaseBroker

            broker = PocketBaseBroker()

        from boss_agent.async_bridge import run_sync

        existing = run_sync(
            broker.candidate_memory.get_candidate_profile(user_id=user_id), timeout=5.0
        )

        return analyze_profile_diff(
            existing,
            state.get("normalized_profile") or {},
            state.get("merge_mode") or "",
        )

    return resume_diff_analyzer_node


def make_resume_persister_node(broker: Any | None = None):
    """Factory creating persistence node saving to candidate_profiles and resume_revisions."""

    def resume_persister_node(state: ResumeLifecycleState) -> dict[str, Any]:
        user_id = state.get("user_id") or "default"
        nonlocal broker
        if not broker:
            from boss_agent.broker import PocketBaseBroker

            broker = PocketBaseBroker()

        from boss_agent.async_bridge import run_sync

        return run_sync(
            persist_resume_profile(
                broker.candidate_memory,
                user_id=user_id,
                normalized_profile=state.get("normalized_profile") or {},
                existing_profile=state.get("existing_profile"),
                merge_mode=state.get("merge_mode") or "initial",
                file_name=state.get("file_name") or "resume.txt",
                raw_resume_text=state.get("raw_resume_text", ""),
                diff_summary=state.get("diff_summary", "画像更新"),
            ),
            timeout=10.0,
        )

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
