"""
tests.unit.test_resume_memory
=============================
Unit tests for resume extraction, structured candidate memory, and idempotent caching.
"""

import json
from unittest.mock import MagicMock

from boss_agent.memory import (
    ResumeMemoryManager,
    ResumeTextExtractor,
    StructuredCandidateProfile,
)


def test_structured_candidate_profile_serialization():
    profile = StructuredCandidateProfile(
        name="张三",
        years_of_experience=8,
        education=[{"school": "清华大学", "degree": "硕士", "major": "计算机"}],
        core_skills=["Python", "Android", "LLM Agent", "Appium"],
        project_highlights=[
            {"name": "自动化 Agent", "description": "构建高可用移动端自动化架构"}
        ],
        target_positions=["AI Agent 专家", "高级 Python 架构师"],
        raw_summary="8年架构经验，主导移动端与大模型结合项目。",
    )

    data = profile.to_dict()
    assert data["name"] == "张三"
    assert data["years_of_experience"] == 8
    assert "Python" in data["core_skills"]

    restored = StructuredCandidateProfile.from_dict(data)
    assert restored.name == profile.name
    assert restored.years_of_experience == profile.years_of_experience
    assert restored.core_skills == profile.core_skills

    prompt_str = restored.format_for_prompt()
    assert "张三" in prompt_str
    assert "8年" in prompt_str
    assert "AI Agent 专家" in prompt_str


def test_resume_text_extractor_text_and_md(tmp_path):
    txt_file = tmp_path / "resume.txt"
    txt_file.write_text("姓名: 李四\n经验: 5年\n技能: Python, PyTorch", encoding="utf-8")

    extractor = ResumeTextExtractor()
    text = extractor.extract_text(txt_file)
    assert "李四" in text
    assert "PyTorch" in text

    md_file = tmp_path / "resume.md"
    md_file.write_text("# 个人简历\n## 王五", encoding="utf-8")
    assert "王五" in extractor.extract_text(md_file)


def test_resume_memory_manager_cache_hit(tmp_path):
    memory_file = tmp_path / "candidate_memory.json"
    memory_data = {
        "name": "王五",
        "years_of_experience": 6,
        "education": [{"school": "浙大", "degree": "学士", "major": "软件工程"}],
        "core_skills": ["Golang", "Kubernetes"],
        "project_highlights": [],
        "target_positions": ["后端架构师"],
        "raw_summary": "精通微服务架构",
    }
    memory_file.write_text(json.dumps(memory_data, ensure_ascii=False), encoding="utf-8")

    mock_llm = MagicMock()
    manager = ResumeMemoryManager(llm_client=mock_llm, memory_file_path=memory_file)

    profile = manager.load_memory(force_refresh=False)
    assert profile.name == "王五"
    assert profile.years_of_experience == 6
    # Verify LLM was NOT called
    mock_llm.chat_completion_json.assert_not_called()


def test_resume_memory_manager_cache_miss_calls_llm(tmp_path):
    memory_file = tmp_path / "candidate_memory.json"
    resume_file = tmp_path / "my_resume.txt"
    resume_file.write_text("赵六，10年开发经验，精通架构与分布式系统。", encoding="utf-8")

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "name": "赵六",
        "years_of_experience": 10,
        "education": [],
        "core_skills": ["分布式系统", "架构设计"],
        "project_highlights": [],
        "target_positions": ["技术总监"],
        "raw_summary": "10年开发经验",
    }

    manager = ResumeMemoryManager(llm_client=mock_llm, memory_file_path=memory_file)
    profile = manager.load_memory(force_refresh=False, resume_file=resume_file)

    assert profile.name == "赵六"
    assert profile.years_of_experience == 10
    mock_llm.chat_completion_json.assert_called_once()

    # Verify memory file was created
    assert memory_file.is_file()
    saved = json.loads(memory_file.read_text(encoding="utf-8"))
    assert saved["name"] == "赵六"
