"""
tests.unit.test_candidate_profile_single_source
===============================================
Unit tests verifying Candidate Profile as the single source of truth:
1. No read or write path treats the local JSON memory file as authoritative.
2. One-time migration lifts an existing local profile into the source of truth,
   with explicit tests for legacy shapes (e.g. dict-shaped core_skills).
3. The profile document's unabbreviated structure survives round trip without loss.
4. Storage failures are reported as failures and never degrade into an empty profile.
5. Incremental Profile Merge flow operates cleanly against the single source.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.candidate_memory_store import (
    InMemoryCandidateMemoryStore,
    migrate_legacy_candidate_profile,
)
from boss_agent.errors import BrokerError, TransportError
from boss_agent.graph import run_resume_lifecycle_graph
from boss_agent.memory import (
    ResumeMemoryManager,
    StructuredCandidateProfile,
)


def test_in_memory_store_isolated_from_local_file(tmp_path, monkeypatch):
    """Verify InMemoryCandidateMemoryStore does not load local JSON file on init."""
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    fake_file = config_dir / "candidate_memory.json"
    fake_file.write_text(
        json.dumps({"name": "历史文件残留", "core_skills": ["C++"]}), encoding="utf-8"
    )

    store = InMemoryCandidateMemoryStore()
    # It must be purely in-memory and empty, ignoring the file on disk
    import asyncio

    profile = asyncio.run(store.get_candidate_profile())
    assert profile is None


def test_legacy_profile_migration_lifts_to_single_source(tmp_path):
    """Verify one-time migration lifts a legacy JSON profile (with dict-shaped skills) into the single source."""
    legacy_file = tmp_path / "legacy_candidate_memory.json"
    legacy_data = {
        "name": "张三",
        "years_of_experience": 8,
        "education": [{"school": "清华大学", "degree": "硕士", "major": "计算机"}],
        "core_skills": {
            "编程语言": ["Python", "Go", "Rust"],
            "AI 架构": ["LangChain", "Multi-Agent", "RAG"],
        },
        "target_positions": ["AI Agent 专家", "资深后端架构师"],
        "raw_summary": "8年大厂核心技术专家，主导多Agent协同系统落地。",
    }
    legacy_file.write_text(json.dumps(legacy_data, ensure_ascii=False), encoding="utf-8")

    broker = InMemoryTaskBroker()
    migrated = migrate_legacy_candidate_profile(local_path=legacy_file, broker=broker)

    assert migrated is not None
    assert migrated["name"] == "张三"
    assert migrated["years_of_experience"] == 8
    # core_skills must be normalized to list of strings
    assert isinstance(migrated["core_skills"], list)
    assert any("Python" in s for s in migrated["core_skills"])
    assert any("LangChain" in s for s in migrated["core_skills"])
    # profile_document is backfilled from raw_summary
    assert migrated["profile_document"] == legacy_data["raw_summary"]

    # Verify single source now returns the migrated profile
    import asyncio

    saved = asyncio.run(broker.candidate_memory.get_candidate_profile())
    assert saved is not None
    assert saved["name"] == "张三"


def test_profile_document_round_trip_unabbreviated():
    """Verify unabbreviated markdown profile document survives broker round trip with 100% fidelity."""
    store = InMemoryCandidateMemoryStore()

    long_doc = (
        "# 候选人全景画像 (Candidate Profile)\n\n"
        "## 1. 核心职业定位与背景概览\n"
        "资深全栈与智能体专家，十年以上架构经验。\n\n"
        "## 2. 核心技术栈与专业能力矩阵\n"
        "- 编程语言: Python, TypeScript, Rust, Go\n"
        "- Agent 编排: LangGraph, AutoGen, CrewAI\n"
        "- 数据与存储: PostgreSQL, PocketBase, Redis, SQLite\n\n"
        "## 3. 核心主导项目与技术攻坚 (Key Projects & Architecture)\n"
        "### 项目 A: 全自动化移动端双轨求职智能体\n"
        "- 架构: 采用 LangGraph 状态机驱动，解耦 UI 驱动器与业务筛选流水线。\n"
        "- 攻坚: 突破移动端无障碍服务风控检测，实现冷热隔离双模式。\n\n"
        "## 4. 可量化成果与标志性突破 (Measurable Achievements)\n"
        "- 吞吐量提升 400%，在极端弱网场景下投递成功率达 99.8%。\n"
    )

    profile_data = {
        "name": "李四",
        "years_of_experience": 10,
        "education": [{"school": "北京大学", "degree": "博士"}],
        "core_skills": ["Python", "Rust", "LangGraph"],
        "target_positions": ["智能体总监", "资深系统架构师"],
        "profile_document": long_doc,
        "raw_summary": long_doc,
    }

    import asyncio

    asyncio.run(store.save_candidate_profile(profile_data))
    fetched = asyncio.run(store.get_candidate_profile())

    assert fetched is not None
    assert fetched["profile_document"] == long_doc
    assert fetched["raw_summary"] == long_doc


def test_storage_failure_propagates_without_degrading():
    """Verify that a storage failure raises an error and NEVER degrades into an empty or absent profile."""
    faulty_broker = MagicMock()
    faulty_broker.candidate_memory.get_candidate_profile = AsyncMock(
        side_effect=TransportError("Database connection lost")
    )
    faulty_broker.candidate_memory.save_candidate_profile = AsyncMock(
        side_effect=BrokerError("Disk write failed")
    )

    # 1. ResumeMemoryManager load_cached_memory must raise TransportError
    with patch("boss_agent.broker.PocketBaseBroker", return_value=faulty_broker):
        manager = ResumeMemoryManager()
        with pytest.raises(TransportError):
            manager.load_cached_memory()

    # 2. ResumeMemoryManager save_memory_profile must raise BrokerError
    with patch("boss_agent.broker.PocketBaseBroker", return_value=faulty_broker):
        manager = ResumeMemoryManager()
        sample_profile = StructuredCandidateProfile(name="王五", years_of_experience=5)
        with pytest.raises(BrokerError):
            manager.save_memory_profile(sample_profile)

    # 3. Resume lifecycle graph must raise when storage fails during diff analyzer
    with pytest.raises(TransportError):
        run_resume_lifecycle_graph(
            raw_resume_text="王五，5年经验",
            file_name="wangwu.txt",
            user_id="user_err",
            broker=faulty_broker,
        )


def test_incremental_merge_flow_against_single_source():
    """Verify incremental merge updates the single source without touching local JSON memory files."""
    broker = InMemoryTaskBroker()

    # Seed single source with initial profile
    initial_profile = {
        "name": "赵六",
        "years_of_experience": 5,
        "core_skills": ["Python", "Flask"],
        "target_positions": ["后端工程师"],
        "profile_document": "# 初始画像\n5年经验后端工程师",
        "raw_summary": "# 初始画像\n5年经验后端工程师",
    }
    import asyncio

    asyncio.run(broker.candidate_memory.save_candidate_profile(initial_profile, user_id="user_123"))

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "name": "赵六",
        "years_of_experience": 6,
        "core_skills": ["Python", "FastAPI", "Docker"],
        "target_positions": ["资深后端工程师", "架构师"],
        "profile_document": "# 升级画像\n6年经验资深后端与架构师",
        "raw_summary": "# 升级画像\n6年经验资深后端与架构师",
    }

    result = run_resume_lifecycle_graph(
        raw_resume_text="赵六，6年经验，掌握FastAPI与Docker",
        file_name="zhaoliu_v2.txt",
        user_id="user_123",
        merge_mode="merge",
        llm_client=mock_llm,
        broker=broker,
    )

    assert result["status"] == "completed"
    final_prof = result["final_profile"]
    assert final_prof["years_of_experience"] == 6
    # Union of core skills: Flask preserved, FastAPI and Docker added
    assert "Flask" in final_prof["core_skills"]
    assert "FastAPI" in final_prof["core_skills"]
    assert "Docker" in final_prof["core_skills"]
    # Union of target positions
    assert "后端工程师" in final_prof["target_positions"]
    assert "架构师" in final_prof["target_positions"]
    # Profile document updated to latest
    assert "升级画像" in final_prof["profile_document"]

    # Verify single source is updated
    updated_in_broker = asyncio.run(
        broker.candidate_memory.get_candidate_profile(user_id="user_123")
    )
    assert updated_in_broker["years_of_experience"] == 6
    assert "Flask" in updated_in_broker["core_skills"]
    assert "升级画像" in updated_in_broker["profile_document"]
