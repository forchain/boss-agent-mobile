"""
tests.unit.test_screening
=========================
Unit tests for screening pipeline, CardFacets digest preservation, and search_filter pass-through.
"""

from unittest.mock import MagicMock

from boss_agent.feed_pipeline import FeedStreamConfig
from boss_agent.memory import StructuredCandidateProfile
from boss_agent.models import FilterConfig, JobCardBrief, JobPosting
from boss_agent.screening import CandidateScreener, CardFacets


def test_card_facets_to_job_posting_preserves_digest():
    """Verify CardFacets.to_job_posting retains self.digest without dropping it."""
    facets = CardFacets(
        title="AI 应用专家",
        company_name="智能互联",
        tags=["硕士优先", "Agent"],
        digest="大模型端侧研发团队急招，算法工程双背景优先",
        salary_range="40-60K",
        location="海淀区",
    )
    posting = facets.to_job_posting("岗位职责：负责智能体工程化系统研发，要求本科及以上。")
    assert posting.digest == "大模型端侧研发团队急招，算法工程双背景优先"
    assert posting.title == "AI 应用专家"
    assert posting.salary_range == "40-60K"
    assert posting.tags == ["硕士优先", "Agent"]


def test_card_facets_from_card_and_to_job_posting_with_search_filter():
    """Verify CardFacets extracts and propagates search_filter from card and arguments."""
    filter_data = {"education": "硕士", "salary": "5万元以上"}
    card_dict = {
        "title": "大模型架构师",
        "company_name": "深度智能",
        "salary_range": "50-80K",
        "digest": "顶尖团队直招，硕士优先",
        "search_filter": filter_data,
    }

    facets = CardFacets.from_card(card_dict)
    assert facets.search_filter == filter_data
    assert facets.digest == "顶尖团队直招，硕士优先"

    posting = facets.to_job_posting("岗位职责：负责分布式多Agent系统研发。")
    assert posting.digest == "顶尖团队直招，硕士优先"
    assert posting.search_filter == filter_data


def test_card_facets_from_card_with_filter_config_object():
    """Verify CardFacets handles FilterConfig instances cleanly by coercing to dict."""
    cfg = FilterConfig(education="硕士", salary="40K以上")
    card_dict = {
        "title": "LLM 算法专家",
        "company_name": "先锋科技",
        "search_filter": cfg,
    }
    facets = CardFacets.from_card(card_dict)
    assert isinstance(facets.search_filter, dict)
    assert facets.search_filter["education"] == "硕士"
    assert facets.search_filter["salary"] == "40K以上"


def test_candidate_screener_evaluate_job_passes_search_filter_to_greeting():
    """Verify CandidateScreener.evaluate_job forwards search_filter into JobPosting and greeting service."""
    screener = CandidateScreener()

    mock_greeting_service = MagicMock()
    mock_greeting_service.evaluate_and_draft_greeting.return_value = MagicMock(
        match_score=92,
        match_reasons=["硕士研究方向与端侧Agent高度吻合"],
        jd_key_requirements=["多Agent系统研发"],
        greeting_message="王总您好,幸会!我硕士阶段专注于智能体研究，期待进一步交流！",
    )
    screener._greeting_service = MagicMock(return_value=mock_greeting_service)

    card = JobCardBrief(
        title="Agent 专家",
        company_name="智元科技",
        recruiter_name="王经理",
        salary_range="40-60K",
        digest="端侧模型研发，硕士优先",
        tags=["硕士优先", "Python"],
    )

    jd_text = "岗位职责：负责智能体协同工作流研发。\n任职资格：计算机相关专业本科及以上学历，3年以上经验。"
    filter_data = {"education": "硕士", "salary": "5万元以上"}

    profile = StructuredCandidateProfile(
        name="李同学",
        years_of_experience=5,
        education=[{"school": "清华大学", "degree": "硕士", "major": "人工智能"}],
    )

    result = screener.evaluate_job(
        card=card,
        jd_text=jd_text,
        profile=profile,
        search_filter=filter_data,
    )

    assert result.passed is True
    assert result.match_score == 92
    assert "硕士" in result.match_reasons[0]
    assert "王总您好,幸会!" in result.greeting_message

    # Ensure evaluate_and_draft_greeting received JobPosting with correct digest and search_filter
    call_kwargs = mock_greeting_service.evaluate_and_draft_greeting.call_args.kwargs
    passed_job: JobPosting = call_kwargs["job"]
    assert passed_job.digest == "端侧模型研发，硕士优先"
    assert passed_job.search_filter == filter_data


def test_feed_stream_config_and_pipeline_filter_coercion():
    """Verify FeedStreamConfig to_dict serialization and filter pass-through readiness."""
    cfg = FilterConfig(education="硕士", salary="5万元以上")
    stream_cfg = FeedStreamConfig(filter_config=cfg)

    filter_dict = stream_cfg.filter_config.to_dict()
    assert filter_dict["education"] == "硕士"
    assert filter_dict["salary"] == "5万元以上"
    assert filter_dict["enable_filter"] is True
