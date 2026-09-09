"""
tests/unit/test_resume_lifecycle_graph.py
=========================================
Unit tests for the LangGraph resume lifecycle state machine.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from boss_agent.graph import (
    build_resume_lifecycle_graph,
    run_resume_lifecycle_graph,
)


@pytest.fixture
def mock_broker():
    broker = MagicMock()
    broker.get_candidate_profile = AsyncMock(return_value=None)
    broker.save_candidate_profile = AsyncMock(side_effect=lambda data, user_id="default": dict(data))
    broker.create_resume_revision = AsyncMock(
        return_value={"id": "rev-001", "file_name": "test_resume.txt", "diff_summary": "测试记录"}
    )
    return broker


@pytest.fixture
def mock_llm_client():
    client = MagicMock()
    client.chat_completion_json.return_value = {
        "name": "李四",
        "years_of_experience": 8,
        "education": ["某重点大学 软件工程 学士"],
        "core_skills": ["Python", "LangChain", "FastAPI", "Docker"],
        "target_positions": ["AI Agent 架构师", "全栈技术专家"],
        "profile_document": (
            "# 候选人全景画像 (Candidate Profile)\n\n"
            "## 1. 核心职业定位与背景概览\n"
            "李四，8年资深架构师。\n\n"
            "## 2. 核心技术栈与专业能力矩阵\n"
            "- Python, LangChain, FastAPI\n\n"
            "## 3. 核心主导项目与技术攻坚 (Key Projects & Architecture)\n"
            "主导智能招聘 Agent 核心架构设计。\n\n"
            "## 4. 可量化成果与标志性突破 (Measurable Achievements)\n"
            "效率提升 300%。\n\n"
            "## 5. 资格认证、语言能力与附加信息\n"
            "CET-6\n"
        ),
        "raw_summary": "李四，8年资深架构师。",
    }
    return client


def test_build_resume_lifecycle_graph():
    """Verify resume lifecycle graph compiles cleanly."""
    graph = build_resume_lifecycle_graph()
    assert graph is not None


def test_run_resume_lifecycle_initial(mock_broker, mock_llm_client):
    """Verify initial resume parsing completes with diff summary and profile persistence."""
    raw_text = "李四，8年研发经验，精通Python、LangChain与FastAPI。期望岗位：AI Agent 架构师。"
    result = run_resume_lifecycle_graph(
        raw_resume_text=raw_text,
        file_name="resume_lisi.txt",
        user_id="user_test",
        llm_client=mock_llm_client,
        broker=mock_broker,
    )

    assert result["status"] == "completed"
    assert "【初次录入】" in result["diff_summary"]
    final_prof = result["final_profile"]
    assert final_prof["name"] == "李四"
    assert final_prof["years_of_experience"] == 8
    assert "AI Agent 架构师" in final_prof["target_positions"]
    assert "Python" in final_prof["core_skills"]
    assert "# 候选人全景画像" in final_prof["profile_document"]
    assert final_prof["raw_summary"] == final_prof["profile_document"]
    assert final_prof["work_experiences"] == []
    assert final_prof["projects"] == []

    mock_broker.save_candidate_profile.assert_called_once()
    mock_broker.create_resume_revision.assert_called_once()


def test_run_resume_lifecycle_incremental_merge(mock_broker, mock_llm_client):
    """Verify incremental resume update merges skills and detects diffs against existing profile."""
    existing_profile = {
        "name": "李四",
        "years_of_experience": 7,
        "core_skills": ["Python", "Django"],
        "target_positions": ["Python后端开发"],
        "profile_document": "旧版全景画像",
        "raw_summary": "旧版全景画像",
    }
    mock_broker.get_candidate_profile = AsyncMock(return_value=existing_profile)

    raw_text = "李四，8年研发经验，精通Python、LangChain、FastAPI。期望岗位：AI Agent 架构师。"
    result = run_resume_lifecycle_graph(
        raw_resume_text=raw_text,
        file_name="resume_lisi_v2.txt",
        user_id="user_test",
        merge_mode="merge",
        llm_client=mock_llm_client,
        broker=mock_broker,
    )

    assert result["status"] == "completed"
    diff_text = result["diff_summary"]
    assert "经验年限: 7年 -> 8年" in diff_text
    assert "新增期望职位" in diff_text

    final_prof = result["final_profile"]
    assert final_prof["years_of_experience"] == 8
    # Merged skills contain both old and new
    assert "Django" in final_prof["core_skills"]
    assert "FastAPI" in final_prof["core_skills"]
    # Merged target positions contain both old and new
    assert "Python后端开发" in final_prof["target_positions"]
    assert "AI Agent 架构师" in final_prof["target_positions"]
    # Document is updated to latest
    assert "# 候选人全景画像" in final_prof["profile_document"]


def test_run_resume_lifecycle_await_review(mock_broker, mock_llm_client):
    """Verify await_review=True stops at diff stage without persisting to broker."""
    raw_text = "李四，8年研发经验。"
    result = run_resume_lifecycle_graph(
        raw_resume_text=raw_text,
        file_name="resume_lisi.txt",
        user_id="user_test",
        await_review=True,
        llm_client=mock_llm_client,
        broker=mock_broker,
    )

    assert result["status"] == "diff_ready"
    assert "diff_summary" in result
    assert result.get("final_profile") is None
    mock_broker.save_candidate_profile.assert_not_called()
    mock_broker.create_resume_revision.assert_not_called()


def test_resume_lifecycle_llm_failure_resilience(mock_broker):
    """Verify graph falls back gracefully using ProfileNormalizer when LLM call fails."""
    faulty_llm = MagicMock()
    faulty_llm.chat_completion_json.side_effect = RuntimeError("API rate limit exceeded")

    raw_text = (
        "姓名：王五\n"
        "10年经验 全栈架构师\n"
        "技能：Python, FastAPI, Docker, K8s, Vue\n"
        "期望职位：全栈技术专家, 架构师\n"
    )

    result = run_resume_lifecycle_graph(
        raw_resume_text=raw_text,
        file_name="wangwu.txt",
        user_id="user_wang",
        llm_client=faulty_llm,
        broker=mock_broker,
    )

    assert result["status"] == "completed"
    final_prof = result["final_profile"]
    assert final_prof["name"] == "王五"
    assert final_prof["years_of_experience"] == 10
    assert len(final_prof["core_skills"]) > 0
    assert len(final_prof["target_positions"]) > 0
    assert "# 候选人全景画像" in final_prof["profile_document"]
    assert final_prof["raw_summary"] == final_prof["profile_document"]
