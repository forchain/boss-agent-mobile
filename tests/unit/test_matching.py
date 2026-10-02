"""
tests.unit.test_matching
========================
Unit tests for JobMatchGreetingService with search filter, tags, and digest injection,
verifying education fit (Master's candidate matching Bachelor's JD with Master's filter/tags).
"""

from unittest.mock import MagicMock

from boss_agent.matching import (
    JobMatchGreetingService,
    format_search_filter,
)
from boss_agent.memory import StructuredCandidateProfile
from boss_agent.models import FilterConfig, JobPosting


def test_format_search_filter_with_dict():
    """Verify format_search_filter produces formatted text for dict input."""
    filter_data = {
        "education": "硕士",
        "salary": "40-60K",
        "experience": "5-10年",
        "activity": "今日活跃",
        "company_scales": ["100-499人", "500-999人"],
        "industries": ["互联网", "人工智能"],
    }
    formatted = format_search_filter(filter_data)
    assert "目标学历: 硕士" in formatted
    assert "薪资要求: 40-60K" in formatted
    assert "经验要求: 5-10年" in formatted
    assert "活跃度: 今日活跃" in formatted
    assert "公司规模: 100-499人, 500-999人" in formatted
    assert "行业要求: 互联网, 人工智能" in formatted


def test_format_search_filter_with_filter_config():
    """Verify format_search_filter handles FilterConfig dataclass objects."""
    cfg = FilterConfig(
        education="硕士",
        salary="5万元以上",
        experience="10年以上",
        company_scales=["1000-9999人"],
        industries=["移动互联网"],
    )
    formatted = format_search_filter(cfg)
    assert "目标学历: 硕士" in formatted
    assert "薪资要求: 5万元以上" in formatted
    assert "经验要求: 10年以上" in formatted
    assert "公司规模: 1000-9999人" in formatted
    assert "行业要求: 移动互联网" in formatted


def test_format_search_filter_empty_or_none():
    assert format_search_filter(None) == "无"
    assert format_search_filter({}) == "无"


def test_master_candidate_bachelor_jd_matching_with_search_filter_and_tags():
    """Verify Master's candidate matching Bachelor's JD with Master's search filter/tags.

    The user prompt must include:
    - 【检索与筛选过滤条件】 (including 目标学历: 硕士)
    - 【卡片要求标签】
    - 【卡片岗位摘要】
    - 【岗位描述(JD)】
    - Explicit guidance elevating the education preference and requiring natural integration.
    """
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "match_score": 95,
        "jd_key_requirements": [
            "计算机或相关专业硕士优先，深入掌握LLM Agent框架",
            "具备端侧大模型工作流编排与移动端工程落地能力",
        ],
        "match_reasons": [
            "硕士期间主攻人工智能与自动化方向，学术背景与检索过滤强偏好高度契合",
            "主导端侧多Agent系统落地，具备从架构设计到工程交付全栈经验",
        ],
        "greeting_message": (
            "王总您好,幸会!看到贵团队在探索端侧Agent工作流。我硕士阶段主攻人工智能方向，"
            "并在工业界主导过移动端轻量化智能体架构落地，与贵司核心技术诉求十分吻合，期待进一步交流！"
        ),
    }

    profile = StructuredCandidateProfile(
        name="李智",
        years_of_experience=7,
        education=[{"school": "上海交通大学", "degree": "硕士", "major": "人工智能与计算机工程"}],
        core_skills=["Python", "LLM Agent", "Android", "LangGraph"],
        raw_summary="7年移动端与AI Agent工程架构经验，硕士毕业于上海交通大学计算机系",
    )

    job = JobPosting(
        title="AI Agent 专家",
        company_name="智元移动互联",
        salary_range="45-70K",
        job_description=(
            "岗位职责：\n"
            "1. 负责移动端与端云协同的大模型智能体工作流编排系统研发；\n"
            "2. 探索端侧轻量化模型与复杂自动化任务的集成；\n"
            "任职资格：\n"
            "1. 计算机相关专业本科及以上学历；\n"
            "2. 5年以上相关研发经验，熟悉主流Agent框架；"
        ),
        digest="大模型端侧智能体团队直招，硕士优先，年终奖丰厚",
        tags=["硕士优先", "LLM", "Android", "Agent工作流"],
        recruiter_name="王建国",
        recruiter_title="技术总监",
        search_filter={"education": "硕士", "salary": "5万元以上"},
    )

    service = JobMatchGreetingService(llm_client=mock_llm, candidate_profile=profile)
    result = service.evaluate_and_draft_greeting(job=job)

    assert result.match_score == 95
    assert any("硕士" in r for r in result.match_reasons)
    assert "王总您好,幸会!" in result.greeting_message
    assert "硕士" in result.greeting_message

    # Inspect the exact prompts sent to the LLM
    call_args = mock_llm.chat_completion_json.call_args[0][0]
    user_prompt = call_args[1]["content"]

    # 1. Structure sections check
    assert "【检索与筛选过滤条件】:" in user_prompt
    assert "目标学历: 硕士" in user_prompt
    assert "薪资要求: 5万元以上" in user_prompt

    assert "【卡片要求标签】:" in user_prompt
    assert "硕士优先" in user_prompt
    assert "Agent工作流" in user_prompt

    assert "【卡片岗位摘要】:" in user_prompt
    assert "大模型端侧智能体团队直招，硕士优先" in user_prompt

    assert "【岗位描述(JD)】:" in user_prompt
    assert "计算机相关专业本科及以上学历" in user_prompt

    # 2. Education fit elevation guidance check
    assert "【匹配评估与打招呼任务指引】:" in user_prompt
    assert "学历与要求偏好升维" in user_prompt
    assert "当检索过滤条件或卡片标签明确包含硕士或硕士优先时" in user_prompt
    assert "升维判定为强偏好" in user_prompt
    assert "必须结合求职者的硕士学历及专业背景" in user_prompt


def test_refine_with_critique_includes_search_filter_and_tags_context():
    """Verify refine_with_critique user content retains search filter, tags, and digest."""
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "revised_greeting": "张总您好,幸会!我硕士阶段主研大模型编排，并在生产环境落地多Agent系统，期待进一步交流！"
    }

    job = JobPosting(
        title="Agent 研发工程师",
        company_name="未来智能",
        salary_range="35-50K",
        job_description="负责智能体平台的底层研发工作，最低学历要求本科。",
        digest="急聘核心研发，硕士优先考虑",
        tags=["硕士优先", "Python", "LangChain"],
        recruiter_name="张总",
        search_filter={"education": "硕士"},
    )

    service = JobMatchGreetingService(llm_client=mock_llm)
    current_greeting = "张总您好,幸会!看到贵司在招Agent工程师，我有丰富经验。"
    critique = "请自然体现我的硕士专业背景，突出学术与工程结合的优势。"

    revised = service.refine_with_critique(
        job=job,
        current_greeting=current_greeting,
        critique=critique,
    )

    assert "张总您好,幸会!" in revised
    assert "硕士" in revised

    messages = mock_llm.chat_completion_json.call_args[0][0]
    user_msg = messages[-1]["content"]

    assert "【检索与筛选过滤条件】:" in user_msg
    assert "目标学历: 硕士" in user_msg
    assert "【卡片要求标签】:" in user_msg
    assert "硕士优先" in user_msg
    assert "【卡片岗位摘要】:" in user_msg
    assert "急聘核心研发，硕士优先考虑" in user_msg
