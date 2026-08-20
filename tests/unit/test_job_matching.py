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
