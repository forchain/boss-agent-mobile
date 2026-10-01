"""
tests.unit.test_job_matching
============================
Unit tests for JobMatchGreetingService and MatchGreetingResult.
"""

from unittest.mock import MagicMock

from boss_agent.job_entities import JobPosting
from boss_agent.matching import JobMatchGreetingService, MatchGreetingResult
from boss_agent.memory import StructuredCandidateProfile


def test_match_greeting_result_serialization():
    res = MatchGreetingResult(
        match_score=88,
        match_reasons=["5年Python与移动端开发经验", "熟悉LLM Agent架构设计"],
        greeting_message="您好！看到贵司招聘AI Agent架构师，我在移动端自动化与大模型结合方面有5年经验，非常契合该岗位需求，期待与您进一步沟通！",
    )

    data = res.to_dict()
    assert data["match_score"] == 88
    assert len(data["match_reasons"]) == 2

    restored = MatchGreetingResult.from_dict(data)
    assert restored.match_score == 88
    assert restored.greeting_message == res.greeting_message


def test_job_match_greeting_service_evaluation():
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "match_score": 92,
        "match_reasons": ["具备大模型Agent实践经验", "精通自动化测试"],
        "greeting_message": "您好！我对贵司职位非常感兴趣，具备丰富的大模型Agent项目落地经验。",
    }

    service = JobMatchGreetingService(llm_client=mock_llm)
    profile = StructuredCandidateProfile(
        name="张三",
        years_of_experience=6,
        core_skills=["Python", "LLM", "Android"],
    )
    job = JobPosting(
        title="AI Agent 专家",
        company_name="智能未来科技",
        salary_range="35-50K",
        job_description="负责Android端智能Agent系统研发，要求精通Python和大模型技术。",
    )

    result = service.evaluate_and_draft_greeting(profile=profile, job=job)

    assert result.match_score == 92
    assert len(result.match_reasons) == 2
    assert "大模型Agent" in result.greeting_message
    mock_llm.chat_completion_json.assert_called_once()


def test_job_match_greeting_service_fallback_on_error():
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.side_effect = RuntimeError("API error")

    service = JobMatchGreetingService(llm_client=mock_llm)
    profile = StructuredCandidateProfile(name="李四")
    job = JobPosting(
        title="Python 后端",
        company_name="某公司",
        salary_range="20-30K",
        job_description="负责公司内部核心数据处理微服务与API接口研发，熟练掌握Python及异步编程框架。",
    )

    result = service.evaluate_and_draft_greeting(profile=profile, job=job)
    assert result.match_score == 50  # Default fallback score
    assert "您好" in result.greeting_message


def test_persistent_candidate_profile_context():
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "match_score": 88,
        "jd_key_requirements": ["深入理解移动端多端通信", "精通LLM Agent工程化"],
        "match_reasons": ["主导过大模型移动端架构落地", "具备完整自动化SDK设计经验"],
        "greeting_message": "针对贵司移动端多端通信与 Agent 落地的挑战，我主导过类似高可用自动化系统架构，期待进一步探讨！",
    }

    profile = StructuredCandidateProfile(
        name="王五",
        years_of_experience=8,
        core_skills=["Python", "Android", "LLM Agent"],
        raw_summary="8年高并发与大模型架构经验",
    )

    # Initialize service with persistent candidate profile
    service = JobMatchGreetingService(llm_client=mock_llm, candidate_profile=profile)
    job = JobPosting(
        title="移动端 Agent 架构师",
        company_name="智能终端科技",
        salary_range="40-60K",
        job_description="负责Android端Agent通信框架与大模型系统研发，落地端侧轻量化智能体工作流。",
    )

    result = service.evaluate_and_draft_greeting(job=job)

    assert result.match_score == 88
    assert len(result.jd_key_requirements) == 2
    assert "移动端多端通信" in result.jd_key_requirements[0]
    assert "Agent" in result.greeting_message

    # Verify the LLM call system prompt contained candidate background and output contract
    # Per ADR 0010: Code keeps structural scaffolding (candidate profile interpolation,
    # JSON output contract); editable prose lives in the Greeting Prompt document.
    messages_passed = mock_llm.chat_completion_json.call_args[0][0]
    system_msg = messages_passed[0]["content"]
    assert "王五" in system_msg
    assert "8年" in system_msg
    assert "[求职者背景画像]" in system_msg
    assert "【输出格式硬性约定】" in system_msg
    assert "【严格 JSON 输出】" in system_msg


def test_full_context_unabbreviated_matching():
    """Verify that full unabbreviated experiences, quantifiable metrics, and ground truth are passed to LLM."""
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "match_score": 96,
        "jd_key_requirements": ["大规模分布式高并发架构", "大模型落地实战"],
        "match_reasons": ["曾主导微服务重构使吞吐量提升300%", "精通Rust与大模型落地"],
        "greeting_message": "针对贵司高并发大模型落地需求，我曾主导系统重构提升300%吞吐量，深度契合该业务痛点！",
    }

    full_profile = StructuredCandidateProfile(
        name="李高级",
        years_of_experience=10,
        education=[{"school": "清华大学", "degree": "硕士", "major": "计算机科学"}],
        core_skills=["Rust", "Python", "大模型 Agent"],
        work_experiences=[
            {
                "company": "全球顶尖人工智能实验室",
                "role": "首席架构师",
                "department": "平台架构部",
                "start_date": "2020.01",
                "end_date": "至今",
                "responsibilities": "主导核心微服务与 Agent 推理运行时重构，带领20人研发团队攻关高并发瓶颈",
                "achievements": "将整体系统吞吐量提升 300%，零故障平稳支撑数千万级 DAU 大促",
                "raw_details": "完整工作履历原文与技术选型文档",
            }
        ],
        projects=[
            {
                "name": "千万级并发 Agent 网关",
                "role": "技术负责人",
                "start_date": "2022.03",
                "end_date": "2023.09",
                "tech_stack": ["Rust", "Tokio", "FastAPI"],
                "description": "面向大模型 API 的流式高并发聚合网关系统",
                "achievements": "P99 延迟降低 45ms，QPS 达到 100,000+",
                "raw_details": "采用异步 Tokio 运行时与零拷贝网络协议解析",
            }
        ],
        target_positions=["首席架构师", "技术总监"],
        raw_summary="10年高性能分布式架构与大模型研发经验",
        raw_resume_text="【完整原始简历全文】李高级拥有十年高性能架构设计经验，主导微服务架构与大模型落地...",
    )

    service = JobMatchGreetingService(llm_client=mock_llm, candidate_profile=full_profile)
    job = JobPosting(
        title="首席分布式架构师",
        company_name="智能前沿科技",
        salary_range="60-90K·16薪",
        job_description="负责高并发网关与大模型落地，要求具备千万级 DAU 架构经验与技术攻坚能力。",
    )

    result = service.evaluate_and_draft_greeting(job=job)
    assert result.match_score == 96
    assert "300%" in result.match_reasons[0]

    # Verify that the LLM system prompt preserved unabbreviated details
    call_args = mock_llm.chat_completion_json.call_args[0][0]
    system_prompt = call_args[0]["content"]

    assert "李高级" in system_prompt
    assert "全球顶尖人工智能实验室" in system_prompt
    assert "吞吐量提升 300%" in system_prompt
    assert "千万级并发 Agent 网关" in system_prompt
    assert "P99 延迟降低 45ms" in system_prompt
    assert "Rust, Tokio, FastAPI" in system_prompt
    assert "[原始简历无损语料 (Ground Truth 参考)]" in system_prompt
    assert "【完整原始简历全文】" in system_prompt


def test_job_match_greeting_service_requires_full_substantive_jd():
    """Greeting generation must raise ValueError if JD is missing or shorter than 30 characters."""
    import pytest

    mock_llm = MagicMock()
    service = JobMatchGreetingService(llm_client=mock_llm)

    # 1. Completely empty JD
    job_empty = JobPosting(
        title="AI工程师",
        company_name="某公司",
        salary_range="30K",
        job_description="",
    )
    with pytest.raises(ValueError, match="Job description is missing or too short"):
        service.evaluate_and_draft_greeting(job=job_empty)

    # 2. Too short JD (e.g. only 15 characters, like a digest)
    job_short = JobPosting(
        title="AI工程师",
        company_name="某公司",
        salary_range="30K",
        job_description="负责大模型与移动端开发",
    )
    with pytest.raises(ValueError, match="Job description is missing or too short"):
        service.evaluate_and_draft_greeting(job=job_short)

    # 3. Placeholder JD
    job_placeholder = JobPosting(
        title="AI工程师",
        company_name="某公司",
        salary_range="30K",
        job_description="无详细岗位描述",
    )
    with pytest.raises(ValueError, match="Job description is missing or too short"):
        service.evaluate_and_draft_greeting(job=job_placeholder)

    # Ensure LLM was never invoked with empty/short JDs
    mock_llm.chat_completion_json.assert_not_called()


def test_greeting_drafter_in_graph_catches_precondition_failure():
    """Greeting drafter node in LangGraph should record failure state rather than crashing when JD is missing."""
    from boss_agent.graph import run_job_application_graph
    from boss_agent.pages import JobCardBrief
    from boss_agent.screening_policy import ScreeningPolicy

    policy = ScreeningPolicy(title_whitelist=["Agent"])
    card = JobCardBrief(
        title="AI Agent研发架构师",
        company_name="前沿智能",
        recruiter_name="技术总监",
        digest="负责核心智能体工作流平台搭建",
    )

    mock_llm = MagicMock()
    # Execute graph with empty jd_text
    state = run_job_application_graph(card=card, policy=policy, jd_text="", llm_client=mock_llm)

    assert state["keyword_pass"] is True
    assert state["deep_screen_pass"] is False
    assert state["status"] == "jd_unavailable"
    assert state["greeting_message"] == ""
    assert "Job description is missing or too short" in state["error_message"]
    # LLM should not have been called for greeting
    mock_llm.chat_completion_json.assert_not_called()


def test_job_match_greeting_service_default_client_uses_realm(monkeypatch):
    """When no client is passed, JobMatchGreetingService uses create_llm_client with realm configuration."""
    from boss_agent import config_realm

    for _key, names in config_realm.ENV_OVERRIDES:
        for name in names:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_MODEL", "matching-realm-model")
    config_realm.invalidate_cache()

    service = JobMatchGreetingService()
    assert service.llm_client is not None
    assert service.llm_client.config.model == "matching-realm-model"


def test_parse_recruiter_title_and_format_prefix():
    from boss_agent.identifier_helpers import (
        format_recruiter_greeting_prefix,
        parse_recruiter_title,
    )

    cases = [
        # xx 女士
        ("张女士", "张女士", "张女士您好,幸会!"),
        ("张女士 · HR", "张女士", "张女士您好,幸会!"),
        ("李女士•资深顾问", "李女士", "李女士您好,幸会!"),
        ("王女士 (HRBP)", "王女士", "王女士您好,幸会!"),
        ("欧阳女士", "欧阳女士", "欧阳女士您好,幸会!"),
        # xx 先生
        ("钟先生", "钟先生", "钟先生您好,幸会!"),
        ("钟先生 · 猎头顾问", "钟先生", "钟先生您好,幸会!"),
        ("司马先生", "司马先生", "司马先生您好,幸会!"),
        # xxx (真名) - Compound surnames
        ("诸葛孔明", "诸葛总", "诸葛总您好,幸会!"),
        ("诸葛亮", "诸葛总", "诸葛总您好,幸会!"),
        ("欧阳六", "欧阳总", "欧阳总您好,幸会!"),
        ("司马迁", "司马总", "司马总您好,幸会!"),
        ("上官婉儿", "上官总", "上官总您好,幸会!"),
        # xxx (真名) - Single surnames
        ("张伟", "张总", "张总您好,幸会!"),
        ("王小明", "王总", "王总您好,幸会!"),
        ("李明", "李总", "李总您好,幸会!"),
        # Existing xx总
        ("张总", "张总", "张总您好,幸会!"),
        ("诸葛总", "诸葛总", "诸葛总您好,幸会!"),
        # Fallbacks (English, numbers, generic role placeholders, empty)
        ("Alice", "", "您好,幸会!"),
        ("Bob Smith", "", "您好,幸会!"),
        ("Tom · HR", "", "您好,幸会!"),
        ("HR", "", "您好,幸会!"),
        ("招聘专员", "", "您好,幸会!"),
        ("猎头顾问", "", "您好,幸会!"),
        ("12345", "", "您好,幸会!"),
        ("", "", "您好,幸会!"),
        (None, "", "您好,幸会!"),
    ]

    for raw, expected_title, expected_prefix in cases:
        assert parse_recruiter_title(raw) == expected_title, (
            f"parse_recruiter_title failed for {raw!r}"
        )
        assert format_recruiter_greeting_prefix(raw) == expected_prefix, (
            f"format_recruiter_greeting_prefix failed for {raw!r}"
        )


def test_ensure_greeting_prefix_normalization():
    from boss_agent.matching import ensure_greeting_prefix

    # 1. Already exact match
    exact = "张女士您好,幸会!看到贵司正在招聘AI架构师..."
    assert ensure_greeting_prefix(exact, "张女士") == exact

    # 2. Chinese punctuation variant normalized
    variant = "张女士您好，幸会！看到贵司正在招聘AI架构师..."
    assert (
        ensure_greeting_prefix(variant, "张女士") == "张女士您好,幸会!看到贵司正在招聘AI架构师..."
    )

    # 3. Redundant generic greeting stripped
    redundant = "您好！看到贵司正在招聘AI架构师..."
    assert (
        ensure_greeting_prefix(redundant, "钟先生 · 猎头顾问")
        == "钟先生您好,幸会!看到贵司正在招聘AI架构师..."
    )

    # 4. Redundant name greeting stripped
    redundant_name = "张伟您好！看到贵司正在招聘AI架构师..."
    assert (
        ensure_greeting_prefix(redundant_name, "张伟")
        == "张总您好,幸会!看到贵司正在招聘AI架构师..."
    )

    # 5. Compound surname greeting
    greeting_comp = "您好，关注到贵司在招Agent专家..."
    assert (
        ensure_greeting_prefix(greeting_comp, "诸葛孔明")
        == "诸葛总您好,幸会!关注到贵司在招Agent专家..."
    )

    # 6. No leading greeting at all
    no_greeting = "针对贵司在大模型架构上的技术诉求，我主导过多智能体项目..."
    assert (
        ensure_greeting_prefix(no_greeting, "欧阳六")
        == "欧阳总您好,幸会!针对贵司在大模型架构上的技术诉求，我主导过多智能体项目..."
    )

    # 7. Fallback when recruiter is None or English
    assert ensure_greeting_prefix("您好！看到贵司招聘...", None) == "您好,幸会!看到贵司招聘..."
    assert ensure_greeting_prefix("您好！看到贵司招聘...", "Alice") == "您好,幸会!看到贵司招聘..."

    # 8. Empty input returns expected prefix alone
    assert ensure_greeting_prefix("", "张女士") == "张女士您好,幸会!"


def test_evaluate_and_draft_greeting_enforces_dynamic_salutation():
    mock_llm = MagicMock()
    # Mock LLM returns generic "您好！" greeting
    mock_llm.chat_completion_json.return_value = {
        "match_score": 90,
        "match_reasons": ["大模型经验契合"],
        "greeting_message": "您好！看到贵司正在招聘AI Agent专家，我具备深入的落地经验。",
    }

    service = JobMatchGreetingService(llm_client=mock_llm)
    job = JobPosting(
        title="AI Agent 专家",
        company_name="智元创新",
        salary_range="40-60K",
        job_description="负责大模型与多智能体系统研发，有端侧落地与通信架构经验优先。",
        recruiter_name="钟先生 · 猎头顾问",
    )

    result = service.evaluate_and_draft_greeting(job=job)

    # 1. Output must strictly begin with dynamic salutation
    assert result.greeting_message.startswith("钟先生您好,幸会!")
    assert (
        result.greeting_message
        == "钟先生您好,幸会!看到贵司正在招聘AI Agent专家，我具备深入的落地经验。"
    )

    # 2. System and user prompts passed to LLM must explicitly mandate the salutation
    messages_passed = mock_llm.chat_completion_json.call_args[0][0]
    user_msg = messages_passed[1]["content"]
    assert "钟先生 (猎头顾问)" in user_msg
    assert "钟先生您好,幸会!" in user_msg
    assert "【打招呼开头称谓硬性要求】" in user_msg


def test_evaluate_and_draft_greeting_fallback_includes_dynamic_salutation():
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.side_effect = RuntimeError("API down")

    service = JobMatchGreetingService(llm_client=mock_llm)
    job = JobPosting(
        title="Python 高级开发",
        company_name="智能未来",
        salary_range="25-35K",
        job_description="负责微服务框架与后台数据流服务，熟练掌握Python异步编程架构。",
        recruiter_name="诸葛孔明",
    )

    result = service.evaluate_and_draft_greeting(job=job)
    assert result.match_score == 50
    assert result.greeting_message.startswith("诸葛总您好,幸会!")
