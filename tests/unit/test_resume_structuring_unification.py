"""
tests/unit/test_resume_structuring_unification.py
=================================================
Tests for Candidate 3 / Ticket #472:
- One core resume-structuring Use Case shared by ResumeLifecycleGraph and ResumeMemoryManager.
- Unified prompt carrying full work_experiences and projects sub-schemas.
- No MiniMax wire sniff or hardcoded max_tokens=16384 outside the LLM client adapter.
- Verification refresh via ResumeMemoryManager.generate_and_save_memory records an append-only
  Resume Revision when persisting to CandidateMemoryStore.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from boss_agent.adapters.memory.candidate_memory_store import InMemoryCandidateMemoryStore
from boss_agent.adapters.outbound.resume.memory import ResumeMemoryManager
from boss_agent.core.candidate.use_cases.resume_lifecycle import generate_profile_document


def test_generate_profile_document_uses_full_subschemas_and_no_wire_quirks() -> None:
    llm = MagicMock()
    llm.config = SimpleNamespace(
        base_url="https://api.minimaxi.com/v1",
        model="MiniMax-M3",
    )
    llm.chat_completion_json.return_value = {
        "name": "张三",
        "years_of_experience": 5,
        "core_skills": ["Python"],
        "target_positions": ["AI 工程师"],
        "profile_document": "# 画像\n张三",
    }

    generate_profile_document(llm, "张三，5年Python与AI Agent开发经验")

    llm.chat_completion_json.assert_called_once()
    args, kwargs = llm.chat_completion_json.call_args
    prompt_text = args[0][1]["content"]
    assert '"responsibilities"' in prompt_text
    assert '"tech_stack"' in prompt_text
    assert "extra_payload" not in kwargs or kwargs["extra_payload"] is None
    assert "max_tokens" not in kwargs


@pytest.mark.asyncio
async def test_memory_manager_refresh_uses_same_prompt_and_records_resume_revision(
    tmp_path,
) -> None:
    resume_file = tmp_path / "resume_refresh.txt"
    resume_file.write_text("李四，8年架构经验，主导分布式智能体系统。", encoding="utf-8")

    store = InMemoryCandidateMemoryStore(load_local_profile=False)
    llm = MagicMock()
    llm.config = SimpleNamespace(
        base_url="https://api.minimaxi.com/v1",
        model="MiniMax-M3",
    )
    llm.chat_completion_json.return_value = {
        "name": "李四",
        "years_of_experience": 8,
        "education": [],
        "core_skills": ["Python", "Distributed Systems"],
        "work_experiences": [{"company": "示例科技", "role": "架构师"}],
        "projects": [{"name": "智能体平台", "role": "负责人"}],
        "target_positions": ["架构师"],
        "profile_document": "# 候选人全景画像\n李四",
    }

    manager = ResumeMemoryManager(llm_client=llm, candidate_memory=store)
    profile = manager.generate_and_save_memory(resume_file)

    assert profile.name == "李四"
    assert len(profile.work_experiences) == 1
    assert len(profile.projects) == 1

    # Must not inject MiniMax wire sniff or hardcoded 16384 outside the LLM adapter
    _args, kwargs = llm.chat_completion_json.call_args
    assert "extra_payload" not in kwargs or kwargs["extra_payload"] is None
    assert "max_tokens" not in kwargs

    # Prompt matches the core structuring Use Case
    generate_profile_document(llm, "李四，8年架构经验，主导分布式智能体系统。")
    first_prompt = llm.chat_completion_json.call_args_list[0].args[0][1]["content"]
    second_prompt = llm.chat_completion_json.call_args_list[1].args[0][1]["content"]
    assert first_prompt == second_prompt

    # Append-only Resume Revision recorded alongside Candidate Profile
    saved_profile = await store.get_candidate_profile()
    assert saved_profile is not None
    assert saved_profile["name"] == "李四"
    revisions = await store.list_resume_revisions()
    assert len(revisions) == 1
    assert revisions[0]["file_name"] == "resume_refresh.txt"
