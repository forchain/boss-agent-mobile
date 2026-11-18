"""
tests/unit/test_feed_verification.py
====================================
Unit tests for ``boss_agent.adapters.inbound.verification.feed_verification``.

Migrated from the legacy ``SmokeHarness`` suites onto the active verification module
(``run_verification_feed``, ``build_verification_pipeline``, ``render_greeting_match_card``)
and ``FeedStreamConfig`` launch contracts.
"""

import io
from unittest.mock import MagicMock, patch

import pytest
from _feed_harness import ScriptedFeed, _card, _detail_page, _posting, scripted_world
from rich.console import Console

from boss_agent.adapters.inbound.verification import feed_verification
from boss_agent.adapters.memory.job_store import InMemoryJobRecordStore
from boss_agent.adapters.outbound.device.pages import ChatPage
from boss_agent.core.candidate.domain.candidate_profile import CandidateProfile
from boss_agent.core.job.domain.job_entities import JobPosting
from boss_agent.core.job.domain.screening_policy import ScreeningPolicy
from boss_agent.core.job.use_cases.matching import MatchGreetingResult
from boss_agent.core.job.use_cases.screening import CandidateScreener, CardScreeningVerdict
from boss_agent.core.search.domain.models import FilterConfig, SearchConfig
from boss_agent.core.shared.enums import ChatButtonState


def _matching_service() -> MagicMock:
    service = MagicMock()
    service.evaluate_and_draft_greeting.return_value = MatchGreetingResult(
        match_score=95,
        match_reasons=["技术栈高度匹配"],
        greeting_message="您好！我对贵司大模型平台岗位非常感兴趣！",
    )
    return service


def _record_console():
    buffer = io.StringIO()
    return buffer, patch(
        "boss_agent.adapters.inbound.verification.feed_verification.console",
        Console(file=buffer, width=200, no_color=True),
    )


def _pipeline(
    driver: MagicMock,
    *,
    matching_service: MagicMock | None = None,
    feed: ScriptedFeed | None = None,
    detail: MagicMock | None = None,
    chat: MagicMock | None = None,
):
    """Build a verification pipeline over a **complete** scripted device world.

    The world is composed whole and injected at construction (issue #481): there is no
    post-construction rebinding of the engine's screens.
    """
    screener = CandidateScreener(llm_client=MagicMock(), matching_service=matching_service)
    return feed_verification.build_verification_pipeline(
        driver,
        screener=screener,
        world=scripted_world(feed=feed, detail=detail, chat=chat),
        store=InMemoryJobRecordStore(),
    )


def test_run_verification_feed_returns_extracted_job_posting():
    """``run_verification_feed`` returns the ``JobPosting`` extracted by ``JobFeedPipeline``."""
    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    posting = JobPosting(
        title="资深 Python / Android 自动化专家",
        company_name="北京智联前沿科技有限公司",
        salary_range="40-65K·16薪",
        job_description=(
            "岗位职责：\n"
            "1. 负责大规模移动端自动化框架设计与高可靠执行引擎开发；\n"
            "2. 深度优化反爬风控拟真轨迹与验证码智能接管策略；\n"
            "3. 具备 5 年以上 Python / Android SDK / Appium 深度实战经验。"
        ),
    )
    pipeline = _pipeline(
        driver,
        feed=ScriptedFeed(
            [[_card("资深 Python / Android 自动化专家", "北京智联前沿科技有限公司")]]
        ),
        detail=_detail_page(posting=posting),
    )

    with patch("time.sleep", return_value=None):
        result = feed_verification.run_verification_feed(
            pipeline=pipeline,
            search_config=SearchConfig(keyword=None),
            filter_config=FilterConfig(
                education=None, salary=None, experience=None, activity=None, company_scales=[]
            ),
            screening_policy=ScreeningPolicy(),
            candidate_profile=None,
            enable_greeting_draft=False,
        )

    assert len(result.postings) == 1
    job = result.postings[0]
    assert isinstance(job, JobPosting)
    assert job.title == "资深 Python / Android 自动化专家"
    assert job.company_name == "北京智联前沿科技有限公司"
    assert job.salary_range == "40-65K·16薪"
    assert "岗位职责" in job.job_description


def test_run_verification_feed_fails_loudly_when_nothing_was_extracted():
    """An empty ``result.postings`` must raise ``RuntimeError`` naming outcome and reason."""
    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    pipeline = _pipeline(
        driver,
        feed=ScriptedFeed([[_card("AI Agent 平台工程师", "智元创新")]]),
        detail=_detail_page(
            posting=JobPosting(
                title="AI Agent 平台工程师",
                company_name="智元创新",
                salary_range="40-60K",
                job_description="主导多智能体系统与自动化框架设计。",
                location_line="上海·浦东新区·陆家嘴",
            )
        ),
    )

    with pytest.raises(RuntimeError) as failure, patch("time.sleep", return_value=None):
        feed_verification.run_verification_feed(
            pipeline=pipeline,
            search_config=SearchConfig(keyword=None),
            filter_config=FilterConfig(
                education=None, salary=None, experience=None, activity=None, company_scales=[]
            ),
            screening_policy=ScreeningPolicy(
                business_district_blacklist=["陆家嘴"], max_commute_distance_km=None
            ),
            candidate_profile=None,
            enable_greeting_draft=False,
        )

    message = str(failure.value)
    assert "filtered_by_app_rule" in message
    assert "陆家嘴" in message


def test_verification_feed_drafts_and_renders_greeting_for_extracted_posting():
    """When enabled with a profile, ``run_verification_feed`` + ``render_greeting_match_card`` drafts & renders."""
    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}
    matching_service = _matching_service()
    candidate_profile = CandidateProfile(
        name="测试候选人",
        years_of_experience=7,
        core_skills=["Python", "Appium", "LLM"],
    )
    pipeline = _pipeline(
        driver,
        matching_service=matching_service,
        feed=ScriptedFeed([[_card("资深 Agent 研发工程师", "未来智能")]]),
        detail=_detail_page(posting=_posting("资深 Agent 研发工程师", "未来智能")),
    )

    with (
        patch.object(feed_verification, "render_match_card") as render_match_card,
        patch("time.sleep", return_value=None),
    ):
        result = feed_verification.run_verification_feed(
            pipeline=pipeline,
            search_config=SearchConfig(keyword=None),
            filter_config=FilterConfig(
                education=None, salary=None, experience=None, activity=None, company_scales=[]
            ),
            screening_policy=ScreeningPolicy(),
            candidate_profile=candidate_profile,
            enable_greeting_draft=True,
        )
        feed_verification.render_greeting_match_card(
            posting=result.postings[0],
            result=result,
            candidate_profile=candidate_profile,
            enable_greeting_draft=True,
        )

    assert result.postings[0].title == "资深 Agent 研发工程师"
    matching_service.evaluate_and_draft_greeting.assert_called_once()
    render_match_card.assert_called_once()
    rendered_job, rendered_match = render_match_card.call_args[0]
    assert rendered_job is result.postings[0]
    assert rendered_match.match_score == 95
    assert (
        rendered_match.greeting_message
        == matching_service.evaluate_and_draft_greeting.return_value.greeting_message
    )


def _headhunter_verification_setup(channel_policy: str, whitelist=None, **policy_overrides):
    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}
    matching_service = _matching_service()
    chat_page = MagicMock(spec=ChatPage)
    pipeline = _pipeline(
        driver,
        matching_service=matching_service,
        feed=ScriptedFeed(
            [[_card("大模型平台负责人", "某人力资源服务公司", recruiter_title="猎头顾问")]]
        ),
        detail=_detail_page(
            state=ChatButtonState.UNCONTACTED,
            posting=_posting("大模型平台负责人", "某人力资源服务公司"),
        ),
        chat=chat_page,
    )
    policy = ScreeningPolicy(
        channel_preference=channel_policy,
        title_whitelist=whitelist or [],
        **policy_overrides,
    )
    profile = CandidateProfile(name="测试候选人", years_of_experience=7, core_skills=["Python"])
    return pipeline, policy, profile, matching_service, chat_page


def test_verification_feed_app_rule_violation_short_circuits_greeting():
    """``direct_only`` + headhunter card without whitelist stops before detail/greeting."""
    pipeline, policy, profile, matching_service, chat_page = _headhunter_verification_setup(
        "direct_only"
    )

    with (
        patch.object(feed_verification, "render_match_card") as render_match_card,
        patch("time.sleep", return_value=None),
        pytest.raises(RuntimeError),
    ):
        feed_verification.run_verification_feed(
            pipeline=pipeline,
            search_config=SearchConfig(keyword=None),
            filter_config=FilterConfig(),
            screening_policy=policy,
            candidate_profile=profile,
            enable_greeting_draft=True,
        )

    matching_service.evaluate_and_draft_greeting.assert_not_called()
    render_match_card.assert_not_called()
    pipeline.world.detail_page.open_chat.assert_not_called()
    chat_page.type_greeting_message.assert_not_called()


def test_verification_feed_relaxed_headhunter_still_drafts_greeting():
    """``direct_only`` + headhunter hitting whitelist keeps greeting drafting alive."""
    pipeline, policy, profile, matching_service, _ = _headhunter_verification_setup(
        "direct_only", whitelist=["大模型"]
    )

    with patch("time.sleep", return_value=None):
        feed_verification.run_verification_feed(
            pipeline=pipeline,
            search_config=SearchConfig(keyword=None),
            filter_config=FilterConfig(),
            screening_policy=policy,
            candidate_profile=profile,
            enable_greeting_draft=True,
        )

    matching_service.evaluate_and_draft_greeting.assert_called_once()


def test_verification_feed_keyword_rejection_spends_no_jd_evaluation():
    """Keyword rejection prints reason on console and skips JD evaluation & greeting."""
    pipeline, policy, profile, matching_service, _ = _headhunter_verification_setup(
        "all", title_blacklist=["平台负责人"]
    )
    buffer, console_patch = _record_console()

    with (
        console_patch,
        patch.object(feed_verification, "render_match_card") as render_match_card,
        patch("time.sleep", return_value=None),
        pytest.raises(RuntimeError),
    ):
        feed_verification.run_verification_feed(
            pipeline=pipeline,
            search_config=SearchConfig(keyword=None),
            filter_config=FilterConfig(),
            screening_policy=policy,
            candidate_profile=profile,
            enable_greeting_draft=True,
        )

    output = buffer.getvalue()
    assert "初筛淘汰" in output
    assert "命中职位黑名单关键词: '平台负责人'" in output
    matching_service.evaluate_and_draft_greeting.assert_not_called()
    render_match_card.assert_not_called()
    pipeline.world.detail_page.open_chat.assert_not_called()


def test_verification_feed_unknown_rejection_stage_spends_zero_tokens():
    """``CardScreeningVerdict.passed`` gates JD evaluation regardless of stage name."""
    pipeline, policy, profile, matching_service, _ = _headhunter_verification_setup("all")
    screener = MagicMock()
    screener.evaluate_card.return_value = CardScreeningVerdict(
        passed=False,
        stage="filtered_by_a_future_rule",
        reason="命中未来新增的过滤规则",
        app_rule_violation="命中未来新增的过滤规则",
    )
    pipeline.screener = screener

    buffer, console_patch = _record_console()
    with (
        console_patch,
        patch.object(feed_verification, "render_match_card") as render_match_card,
        patch("time.sleep", return_value=None),
        pytest.raises(RuntimeError),
    ):
        feed_verification.run_verification_feed(
            pipeline=pipeline,
            search_config=SearchConfig(keyword=None),
            filter_config=FilterConfig(),
            screening_policy=policy,
            candidate_profile=profile,
            enable_greeting_draft=True,
        )

    screener.evaluate_job.assert_not_called()
    matching_service.evaluate_and_draft_greeting.assert_not_called()
    render_match_card.assert_not_called()
