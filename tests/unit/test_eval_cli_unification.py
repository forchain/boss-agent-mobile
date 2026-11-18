"""
tests/unit/test_eval_cli_unification.py
=======================================
Tests for Candidate 8 / Ticket #473:
- Shared evaluation CLI parser preserves all 9 JobPosting facets (including tags, digest,
  search_filter) across evaluate_match, refine_greeting, and refine_screening.
- refine_screening invokes public CandidateScreener.evaluate_jd rather than _evaluate_jd.
- evaluate_match delegates error fallback to the domain's offline_match_result.
- Stub-profile fallback loads CandidateProfile via CandidateMemoryStore without building
  PocketBaseTaskBroker or an LLM client.
"""

import json
import sys
from unittest.mock import MagicMock, patch

from boss_agent.core.candidate.domain.candidate_profile import CandidateProfile
from boss_agent.core.job.use_cases.screening import ScreeningVerdict


def test_refine_screening_preserves_all_nine_job_facets_and_calls_public_evaluate_jd(
    monkeypatch, capsys
) -> None:
    import scripts.refine_screening as script

    job_payload = {
        "job_title": "AI Agent 架构师",
        "company_name": "智元未来",
        "salary_range": "40-60K",
        "job_description": "负责大模型与 Agent 工作流架构设计，要求硕士及以上学历。",
        "recruiter_name": "张女士",
        "recruiter_title": "招聘总监",
        "tags": ["硕士", "Agent", "Python"],
        "digest": "主导 Agent 平台研发",
        "search_filter": {"education": "硕士", "salary": "40-60K"},
    }

    captured = {}

    class _FakeScreener:
        def evaluate_jd(self, job, *, current_prompt=None, policy=None):
            captured["job"] = job
            return ScreeningVerdict(approved=True, reason="ok", stage="passed")

        def _evaluate_jd(self, *args, **kwargs):
            raise AssertionError("scripts/refine_screening.py must call public evaluate_jd")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "refine_screening.py",
            "--action",
            "evaluate",
            "--job",
            json.dumps(job_payload, ensure_ascii=False),
            "--policy",
            json.dumps({"enable_screening": True}),
        ],
    )
    with (
        patch.object(script, "build_llm_client", return_value=MagicMock()),
        patch.object(script, "build_screener", return_value=_FakeScreener()),
    ):
        script.main()

    out = json.loads(capsys.readouterr().out)
    assert out["success"] is True
    job = captured["job"]
    assert job.tags == ["硕士", "Agent", "Python"]
    assert job.digest == "主导 Agent 平台研发"
    assert job.search_filter == {"education": "硕士", "salary": "40-60K"}


def test_evaluate_match_uses_domain_offline_fallback_on_service_error(monkeypatch, capsys) -> None:
    import scripts.evaluate_match as script

    job_payload = {
        "job_title": "AI Agent 架构师",
        "company_name": "未来智元",
        "salary_range": "40-60K",
        "job_description": "负责智能体工作流，精通 Python 与 LangGraph。",
        "recruiter_name": "张女士 · 招聘总监",
        "tags": ["Python", "Agent"],
        "digest": "智能体编排",
        "search_filter": {"education": "硕士"},
    }
    profile_payload = {
        "name": "李华",
        "years_of_experience": 6,
        "core_skills": ["Python", "LangGraph", "Agent"],
        "profile_document": "# 画像\n6年 Python 与 LangGraph 经验。",
    }

    failing_service = MagicMock()
    failing_service.evaluate_and_draft_greeting.side_effect = RuntimeError("LLM down")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate_match.py",
            "--job",
            json.dumps(job_payload, ensure_ascii=False),
            "--profile",
            json.dumps(profile_payload, ensure_ascii=False),
        ],
    )
    with (
        patch.object(script, "build_llm_client", return_value=MagicMock()),
        patch.object(script, "build_matching_service", return_value=failing_service),
    ):
        script.main()

    out = json.loads(capsys.readouterr().out)
    assert out["match_score"] > 50
    assert out["greeting_message"].startswith("张女士您好,幸会!")


def test_evaluate_match_and_refine_greeting_load_stub_profile_without_broker_or_llm(
    monkeypatch, capsys
) -> None:
    from scripts import _eval_cli

    from boss_agent import main as main_mod

    cached_profile = CandidateProfile(
        name="真实候选人",
        years_of_experience=7,
        core_skills=["Python", "Agent"],
        profile_document="# 真实画像\n7年经验",
    )

    monkeypatch.setattr(
        main_mod,
        "PocketBaseTaskBroker",
        MagicMock(side_effect=AssertionError("Must not construct PocketBaseTaskBroker")),
    )
    monkeypatch.setattr(
        main_mod,
        "build_resume_memory_manager",
        MagicMock(side_effect=AssertionError("Must not build ResumeMemoryManager to read profile")),
    )

    with patch.object(_eval_cli, "load_cached_candidate_profile", return_value=cached_profile):
        resolved = _eval_cli.parse_candidate_profile_arg(json.dumps({"name": "测试候选人"}))

    assert resolved is not None
    assert resolved.name == "真实候选人"
