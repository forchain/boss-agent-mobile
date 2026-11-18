"""
tests.unit.test_tracing_span_names
==================================
The tracing adapter must reproduce the spans core used to own.

Span names and run types key dashboards and historical traces, so moving the decorators
out of ``core`` is only safe if the traced construction point emits the identical names.
This is the name-preservation pin for ticket #458.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from langsmith.run_helpers import tracing_context
from langsmith.run_trees import RunTree

from boss_agent.adapters.outbound.llm.tracing import (
    TracedCandidateScreener,
    TracedJobMatchGreetingService,
)
from boss_agent.adapters.outbound.resume.memory import ResumeMemoryManager
from boss_agent.core.job.domain.job_entities import JobCardBrief, JobPosting
from boss_agent.core.job.domain.screening_policy import ScreeningPolicy

_JD = "岗位职责：负责基于大模型的多智能体协同编排平台研发，要求熟悉 Python 与异步编程。"


def _spans(call) -> dict:
    """Run `call` under a mock-client root and flatten the resulting run tree."""
    root = RunTree(name="tracing_root", run_type="chain", client=MagicMock())
    with tracing_context(parent=root, client=root.client):
        call()

    found: dict = {}

    def walk(node) -> None:
        found[node.name] = node
        for child in node.child_runs or []:
            walk(child)

    walk(root)
    return found


def _card() -> JobCardBrief:
    return JobCardBrief(
        title="Python Engineer",
        company_name="Acme",
        recruiter_name="Bob",
        tags=["Python"],
    )


def _job() -> JobPosting:
    return JobPosting(
        title="Python Engineer",
        company_name="Acme",
        salary_range="20-30K",
        recruiter_name="Bob",
        job_description=_JD,
    )


def _llm(payload: dict) -> MagicMock:
    client = MagicMock()
    client.chat_completion_json.return_value = payload
    return client


def test_core_job_use_case_spans_keep_their_names_and_run_types() -> None:
    policy = ScreeningPolicy()

    def _screen_llm(messages, *args, **kwargs):
        if "重写这份 Screening Prompt" in messages[0]["content"]:
            return {"prompt": "new"}
        return {"approved": True, "reason": "ok"}

    screener = TracedCandidateScreener(llm_client=_llm({"approved": True, "reason": "ok"}))
    screener.llm_client.chat_completion_json.side_effect = _screen_llm

    greet_llm = MagicMock()
    greet_llm.chat_completion_json.side_effect = [
        {
            "match_score": 80,
            "match_reasons": [],
            "jd_key_requirements": [],
            "greeting_message": "hi",
        },
        {"revised_greeting": "hello"},
        {"prompt": "new"},
    ]
    greeting = TracedJobMatchGreetingService(llm_client=greet_llm)

    spans = _spans(
        lambda: (
            screener.evaluate_card(_card(), policy),
            screener.evaluate_job(_card(), _JD, policy=policy, draft_greeting=False),
            screener.retest_with_critique(_job(), "critique", policy=policy),
            screener.evaluate_jd(_job(), policy=policy),
            screener.refine_screening_prompt(_job(), "critique"),
            greeting.evaluate_and_draft_greeting(_job()),
            greeting.refine_with_critique(_job(), "old", "critique"),
            greeting.refine_greeting_prompt(_job(), "old", "new"),
        )
    )

    expected = {
        "CandidateScreener.evaluate_card",
        "CandidateScreener.evaluate_job",
        "CandidateScreener.screen_jd_semantics",
        "CandidateScreener.retest_with_critique",
        "CandidateScreener.evaluate_jd",
        "CandidateScreener.refine_screening_prompt",
        "JobMatchGreetingService.evaluate_and_draft_greeting",
        "JobMatchGreetingService.refine_with_critique",
        "JobMatchGreetingService.refine_greeting_prompt",
    }
    assert expected <= set(spans)
    for name in expected:
        assert spans[name].run_type == "chain", name

    # The private alias must keep pointing at the traced callable.
    assert screener._evaluate_jd == screener.evaluate_jd


def test_resume_memory_manager_spans_keep_their_names_and_run_types(tmp_path: Path) -> None:
    resume = tmp_path / "resume.txt"
    resume.write_text("张三 8年经验 清华大学计算机硕士", encoding="utf-8")
    memory = tmp_path / "memory.json"

    llm = MagicMock()
    llm.chat_completion_json.return_value = {
        "name": "张三",
        "years_of_experience": 8,
        "core_skills": ["Python"],
        "education": [],
        "work_experiences": [],
        "projects": [],
        "target_positions": [],
    }
    manager = ResumeMemoryManager(llm_client=llm, memory_file_path=memory)

    spans = _spans(
        lambda: (
            manager.generate_and_save_memory(resume),
            manager.load_memory(),
        )
    )

    assert spans["ResumeMemoryManager.generate_and_save_memory"].run_type == "chain"
    assert spans["ResumeMemoryManager.load_memory"].run_type == "tool"
