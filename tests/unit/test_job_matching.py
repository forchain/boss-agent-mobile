"""
tests.unit.test_job_matching
============================
Unit tests for JobMatchGreetingService and MatchGreetingResult.
"""

from unittest.mock import MagicMock

from boss_agent.matching import JobMatchGreetingService, MatchGreetingResult
from boss_agent.memory import StructuredCandidateProfile
from boss_agent.models import JobPosting


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
        job_description="Python 开发",
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
        job_description="负责Android端Agent通信框架与大模型系统研发",
    )

    result = service.evaluate_and_draft_greeting(job=job)

    assert result.match_score == 88
    assert len(result.jd_key_requirements) == 2
    assert "移动端多端通信" in result.jd_key_requirements[0]
    assert "Agent" in result.greeting_message

    # Verify the LLM call system prompt contained candidate background
    messages_passed = mock_llm.chat_completion_json.call_args[0][0]
    system_msg = messages_passed[0]["content"]
    assert "王五" in system_msg
    assert "8年" in system_msg
    assert "【打招呼破冰铁律与原则】" in system_msg

