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
        project_highlights=[{"name": "自动化 Agent", "description": "构建高可用移动端自动化架构"}],
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


def test_resume_memory_manager_loads_from_candidate_config_yaml(tmp_path):
    candidate_config_file = tmp_path / "candidate.local.yaml"
    resume_file = tmp_path / "configured_resume.txt"
    memory_file = tmp_path / "configured_memory.json"
    resume_file.write_text("孙七，5年开发经验", encoding="utf-8")

    candidate_config_file.write_text(
        f"""
resume_path: "{resume_file}"
memory_path: "{memory_file}"
""",
        encoding="utf-8",
    )

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "name": "孙七",
        "years_of_experience": 5,
        "education": [],
        "core_skills": ["Python"],
        "project_highlights": [],
        "target_positions": ["工程师"],
        "raw_summary": "5年经验",
    }

    manager = ResumeMemoryManager(
        llm_client=mock_llm,
        candidate_config_path=candidate_config_file,
    )
    profile = manager.load_memory()

    assert profile.name == "孙七"
    assert profile.years_of_experience == 5
    assert memory_file.is_file()


def test_structured_candidate_profile_normalizes_dict_skills():
    data = {
        "name": "周黄金",
        "years_of_experience": 19,
        "education": [{"school": "沙迦美国大学", "degree": "硕士", "major": "计算机工程"}],
        "core_skills": {
            "AI与智能体": ["Claude", "Codex", "Langchain"],
            "编程语言": ["Python", "Golang"],
        },
        "project_highlights": [{"name": "项目1", "description": "描述1"}],
        "target_positions": ["架构师"],
        "raw_summary": "19年经验",
    }
    profile = StructuredCandidateProfile.from_dict(data)
    assert profile.name == "周黄金"
    assert len(profile.core_skills) == 2
    assert "AI与智能体: Claude, Codex, Langchain" in profile.core_skills
    assert "编程语言: Python, Golang" in profile.core_skills


def test_unabbreviated_work_and_project_experiences_serialization():
    work_exps = [
        {
            "company": "前沿人工智能实验室",
            "role": "首席 Agent 架构师",
            "start_date": "2023-01",
            "end_date": "至今",
            "department": "移动端自动化工程部",
            "responsibilities": "主导 Appium 与大模型推理编排的底层架构设计，搭建高拟人化触控合成引擎。",
            "achievements": "将 Boss 直聘自动化投递成功率由 65% 提升至 98.5%，单日吞吐达 500+ 岗位，防风控触发率为 0。",
            "raw_details": "深入研究 Android 无障碍与 UI Automator 协议，开发自适应手势贝塞尔算法。",
        }
    ]
    projects = [
        {
            "name": "Boss Agent Mobile",
            "role": "项目负责人",
            "start_date": "2024-03",
            "end_date": "2024-09",
            "tech_stack": ["Python", "FastAPI", "Appium", "SvelteKit", "PocketBase"],
            "description": "基于大模型与 Android 原生自动化的全闭环求职智能体系统。",
            "achievements": "实现端到端任务流认领、HITL 滑块人工接管接力与多维度反模板化打招呼生成。",
            "raw_details": "引入 State Stream Broker 与心跳自愈守护机制，任务超时自动释放重试。",
        }
    ]

    profile = StructuredCandidateProfile(
        name="资深技术专家",
        years_of_experience=12,
        education=[{"school": "北京大学", "degree": "硕士", "major": "计算机应用技术"}],
        core_skills=["Python", "LLM Agent", "Android Automation"],
        work_experiences=work_exps,
        projects=projects,
        target_positions=["AI Agent 架构师", "技术总监"],
        raw_summary="12年架构经验，在大模型与移动端自动化领域具备全闭环落地能力。",
        raw_resume_text="这是候选人的完整原始简历全文，包含详细技术文章、开源项目与专利列表。",
    )

    data = profile.to_dict()
    assert len(data["work_experiences"]) == 1
    assert data["work_experiences"][0]["company"] == "前沿人工智能实验室"
    assert "98.5%" in data["work_experiences"][0]["achievements"]
    assert len(data["projects"]) == 1
    assert data["projects"][0]["name"] == "Boss Agent Mobile"
    assert "PocketBase" in data["projects"][0]["tech_stack"]
    assert data["raw_resume_text"] != ""

    restored = StructuredCandidateProfile.from_dict(data)
    assert len(restored.work_experiences) == 1
    assert restored.work_experiences[0]["company"] == "前沿人工智能实验室"
    assert len(restored.projects) == 1
    assert restored.projects[0]["name"] == "Boss Agent Mobile"

    prompt_str = restored.format_for_prompt()
    assert "【前沿人工智能实验室】首席 Agent 架构师" in prompt_str
    assert "核心业绩与量化成果: 将 Boss 直聘自动化投递成功率由 65% 提升至 98.5%" in prompt_str
    assert "【Boss Agent Mobile】 (角色: 项目负责人)" in prompt_str
    assert "技术栈: Python, FastAPI, Appium, SvelteKit, PocketBase" in prompt_str
    assert "[原始简历无损语料 (Ground Truth 参考)]" in prompt_str
    assert "开源项目与专利列表" in prompt_str


def test_sqlite_single_source_roundtrip(tmp_path, monkeypatch):
    from boss_agent.broker import PocketBaseBroker
    from boss_agent.broker.provisioner import provision_pocketbase_sqlite

    db_file = tmp_path / "data.db"
    monkeypatch.setenv("PB_DB_PATH", str(db_file))

    import sqlite3
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE _collections (
            id TEXT PRIMARY KEY,
            system BOOLEAN DEFAULT FALSE,
            type TEXT DEFAULT "base",
            name TEXT UNIQUE NOT NULL,
            fields JSON DEFAULT "[]" NOT NULL,
            indexes JSON DEFAULT "[]" NOT NULL,
            listRule TEXT DEFAULT NULL,
            viewRule TEXT DEFAULT NULL,
            createRule TEXT DEFAULT NULL,
            updateRule TEXT DEFAULT NULL,
            deleteRule TEXT DEFAULT NULL,
            options JSON DEFAULT "{}" NOT NULL,
            created TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%fZ')),
            updated TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%fZ'))
        )
    """)
    conn.commit()
    conn.close()

    # Provision SQLite DB
    provision_pocketbase_sqlite(db_file)

    broker = PocketBaseBroker(
        base_url="http://127.0.0.1:9999"
    )  # Unreachable port to force SQLite fallback

    import asyncio

    profile_data = {
        "name": "李四",
        "years_of_experience": 8,
        "education": [{"school": "复旦大学", "degree": "硕士", "major": "软件工程"}],
        "core_skills": ["Rust", "Python", "FastAPI"],
        "work_experiences": [
            {
                "company": "某前沿科技",
                "role": "后端总监",
                "responsibilities": "搭建微服务框架",
                "achievements": "性能提升300%",
            }
        ],
        "projects": [
            {
                "name": "高并发网关",
                "role": "主导人",
                "tech_stack": ["Rust", "Tokio"],
                "description": "百万级连接网关",
                "achievements": "支撑双十一大促",
            }
        ],
        "target_positions": ["后端总监"],
        "raw_summary": "8年高性能服务开发经验",
        "raw_resume_text": "李四完整的原始履历原文...",
    }

    # Save candidate profile into SQLite fallback
    saved = asyncio.run(broker.save_candidate_profile(profile_data, user_id="test_candidate"))
    assert saved["name"] == "李四"

    # Query candidate profile back from SQLite fallback
    queried = asyncio.run(broker.get_candidate_profile(user_id="test_candidate"))
    assert queried is not None
    assert queried["name"] == "李四"
    assert queried["years_of_experience"] == 8
    assert len(queried["work_experiences"]) == 1
    assert queried["work_experiences"][0]["company"] == "某前沿科技"
    assert "300%" in queried["work_experiences"][0]["achievements"]
    assert len(queried["projects"]) == 1
    assert queried["projects"][0]["name"] == "高并发网关"
    assert queried["raw_resume_text"] == "李四完整的原始履历原文..."

    # Test resume revisions
    rev = asyncio.run(
        broker.create_resume_revision(
            {
                "file_name": "resume_2026.md",
                "file_type": "md",
                "file_size": 2048,
                "extracted_text": "# 李四简历\n...",
                "diff_summary": "+ 新增 2024-2026 后端总监履历",
            },
            user_id="test_candidate",
        )
    )
    assert rev["id"] is not None

    revs = asyncio.run(broker.list_resume_revisions(user_id="test_candidate"))
    assert len(revs) == 1
    assert revs[0]["file_name"] == "resume_2026.md"
    assert revs[0]["diff_summary"] == "+ 新增 2024-2026 后端总监履历"
