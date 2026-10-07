"""
tests.unit.test_smoke_harness_greeting
======================================
SmokeHarness integration with resume memory, match scoring, and greeting drafting.

The greeting used to be drafted by this module's own LangGraph walk over a posting the
harness had extracted itself, which meant the assertions here had to reach into the
harness's page objects to stub the extraction. Issue #390 collapses that walk into
``JobFeedPipeline``: the run screens the card, drafts the greeting and reports what it
drafted, and these tests drive it through the scripted device that the pipeline's own
suites share.

The claim each test pins is unchanged — a drafted greeting reaches the operator's match
card, and a posting turned down by an App-Enforced Filter never gets one.
"""

from unittest.mock import MagicMock, patch

import pytest
from _feed_harness import ScriptedFeed, _card, _detail_page, _posting, script_pages

from boss_agent.enums import ChatButtonState
from boss_agent.matching import MatchGreetingResult
from boss_agent.memory import StructuredCandidateProfile
from boss_agent.pages import ChatPage
from boss_agent.screening_policy import ScreeningPolicy
from boss_agent.search_entities import FilterConfig
from boss_agent.workflows import SmokeHarness, TakeoverHandler

_HEADHUNTER_JD = (
    "主导企业级大模型应用与Agent工作流平台建设，负责推理链编排、"
    "向量检索体系优化以及多智能体协同框架的架构设计。"
)


def _harness(driver, matching_service, **kwargs) -> SmokeHarness:
    """A harness whose greeting is drafted by the injected matching service."""
    memory_manager = MagicMock()
    memory_manager.load_memory.return_value = StructuredCandidateProfile(
        name="测试候选人",
        years_of_experience=7,
        core_skills=["Python", "Appium", "LLM"],
    )
    return SmokeHarness(
        driver=driver,
        takeover_handler=TakeoverHandler(driver, auto_confirm_for_test=True),
        memory_manager=memory_manager,
        matching_service=matching_service,
        filter_config=FilterConfig(
            education=None, salary=None, experience=None, activity=None, company_scales=[]
        ),
        preview_timeout_sec=0.01,
        enable_greeting_draft=True,
        **kwargs,
    )


def _matching_service() -> MagicMock:
    """A matching service whose draft is fixed, so the run's report is predictable."""
    service = MagicMock()
    service.evaluate_and_draft_greeting.return_value = MatchGreetingResult(
        match_score=95,
        match_reasons=["技术栈高度匹配"],
        greeting_message="您好！我对贵司大模型平台岗位非常感兴趣！",
    )
    return service


def test_smoke_harness_drafts_and_renders_the_greeting_for_the_extracted_posting():
    """The greeting is drafted by the matching service the caller injected.

    The harness passes *its* ``JobMatchGreetingService`` into the screener the pipeline
    runs, so the run drafts through the same object the caller holds — that is what makes
    a smoke test's greeting the same artefact a worker's would be. The draft is then
    reported back for the posting that was actually extracted, and rendered: rendering is
    presentation, and the pipeline does not present.
    """
    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}
    matching_service = _matching_service()
    harness = _harness(driver, matching_service)
    script_pages(
        harness.pipeline,
        feed=ScriptedFeed([[_card("资深 Agent 研发工程师", "未来智能")]]),
        detail=_detail_page(posting=_posting("资深 Agent 研发工程师", "未来智能")),
    )

    with patch("time.sleep", return_value=None):
        job = harness.run_smoke_test()

    assert job.title == "资深 Agent 研发工程师"
    harness.memory_manager.load_memory.assert_called_once()
    matching_service.evaluate_and_draft_greeting.assert_called_once()
    matching_service.render_match_card.assert_called_once()
    rendered_job, rendered_match = matching_service.render_match_card.call_args[0]
    assert rendered_job is job
    assert rendered_match.match_score == 95
    assert (
        rendered_match.greeting_message
        == matching_service.evaluate_and_draft_greeting.return_value.greeting_message
    )


def _headhunter_smoke_harness(channel_policy, whitelist=None):
    """A harness whose feed's only card is a headhunter posting."""
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    matching_service = _matching_service()
    harness = _harness(
        mock_driver,
        matching_service,
        screening_policy=ScreeningPolicy(
            channel_preference=channel_policy,
            title_whitelist=whitelist or [],
        ),
    )
    chat_page = MagicMock(spec=ChatPage)
    # A headhunter posting carries the channel on the card the scanner sees, which is
    # where the App-Enforced Filter reads it from.
    script_pages(
        harness.pipeline,
        feed=ScriptedFeed(
            [[_card("大模型平台负责人", "某人力资源服务公司", recruiter_title="猎头顾问")]]
        ),
        detail=_detail_page(
            state=ChatButtonState.UNCONTACTED,
            posting=_posting("大模型平台负责人", "某人力资源服务公司"),
        ),
        chat=chat_page,
    )
    return harness, matching_service, chat_page


def test_smoke_harness_app_rule_violation_short_circuits_greeting():
    """direct_only + a headhunter posting without whitelist rescue.

    The App-Enforced Filter turns the card down before a detail page is ever worth opening,
    so no greeting is drafted, none is rendered, and nothing reaches a chat window — the
    run must stop there rather than message a posting it just rejected.
    """
    harness, matching_service, chat_page = _headhunter_smoke_harness("direct_only")

    with patch("time.sleep", return_value=None), pytest.raises(RuntimeError):
        harness.run_smoke_test()

    matching_service.evaluate_and_draft_greeting.assert_not_called()
    matching_service.render_match_card.assert_not_called()
    harness.pipeline.detail_page.open_chat.assert_not_called()
    chat_page.type_greeting_message.assert_not_called()


def test_smoke_harness_relaxed_headhunter_still_drafts_greeting():
    """direct_only + a headhunter posting hitting the whitelist: relaxation rescue keeps
    the normal greeting draft path alive."""
    harness, matching_service, _ = _headhunter_smoke_harness("direct_only", whitelist=["大模型"])

    with patch("time.sleep", return_value=None):
        harness.run_smoke_test()

    matching_service.evaluate_and_draft_greeting.assert_called_once()
