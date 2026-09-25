"""
tests.unit.test_smoke_harness_greeting
======================================
Unit tests for SmokeHarness integration with resume memory, match scoring, and greeting draft typing.
"""

from unittest.mock import MagicMock, patch

from boss_agent.matching import MatchGreetingResult
from boss_agent.memory import StructuredCandidateProfile
from boss_agent.workflows import SmokeHarness, TakeoverHandler


def test_smoke_harness_runs_matching_and_types_greeting():
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_btn = MagicMock()
    mock_btn.rect = {"x": 50, "y": 50, "width": 100, "height": 50}

    mock_title_elem = MagicMock()
    mock_title_elem.text = "资深 Agent 研发工程师"

    mock_company_elem = MagicMock()
    mock_company_elem.text = "未来智能"

    mock_salary_elem = MagicMock()
    mock_salary_elem.text = "40-60K"

    mock_desc_elem = MagicMock()
    # A realistic JD: greeting drafting is gated on a substantive description, so a
    # terse fixture would never reach the matching service at all.
    mock_desc_elem.text = (
        "岗位职责：负责移动端自动化框架与大模型能力结合的研发工作，"
        "要求精通 Python、Appium 与多智能体编排。"
    )

    step_in_chat = False

    def mock_find_elements(by, value):
        nonlocal step_in_chat
        if "tv_job_name" in value:
            return [mock_title_elem]
        if "tv_company_name" in value:
            return [mock_company_elem]
        if "tv_job_salary" in value:
            return [mock_salary_elem]
        if "tv_description" in value or "tv_job_desc" in value:
            return [mock_desc_elem]
        if "btn_chat" in value or "立即沟通" in value:
            step_in_chat = True
            return [mock_btn]
        if "editText_with_scrollbar" in value or "chat_editor" in value:
            return [mock_btn] if step_in_chat else []
        if "chat" in value:
            return [mock_btn] if step_in_chat else []
        return [mock_btn]

    mock_driver.find_elements.side_effect = mock_find_elements

    mock_memory_mgr = MagicMock()
    mock_profile = StructuredCandidateProfile(
        name="测试候选人",
        years_of_experience=7,
        core_skills=["Python", "Appium", "LLM"],
    )
    mock_memory_mgr.load_memory.return_value = mock_profile

    mock_matching_svc = MagicMock()
    mock_match_result = MatchGreetingResult(
        match_score=95,
        match_reasons=["技术栈高度匹配", "多年自动化经验"],
        greeting_message="您好！我对贵司资深 Agent 研发工程师岗位非常感兴趣！",
    )
    mock_matching_svc.evaluate_and_draft_greeting.return_value = mock_match_result

    takeover = TakeoverHandler(mock_driver, auto_confirm_for_test=True)
    harness = SmokeHarness(
        driver=mock_driver,
        takeover_handler=takeover,
        memory_manager=mock_memory_mgr,
        matching_service=mock_matching_svc,
        preview_timeout_sec=0.01,
        enable_greeting_draft=True,
    )

    with patch("time.sleep", return_value=None):
        job = harness.run_smoke_test()

    assert job.title == "资深 Agent 研发工程师"
    mock_memory_mgr.load_memory.assert_called_once()
    mock_matching_svc.evaluate_and_draft_greeting.assert_called_once()
    mock_matching_svc.render_match_card.assert_called_once_with(job, mock_match_result)


def _headhunter_smoke_harness(channel_policy, whitelist=None):
    """Build a SmokeHarness whose detail page yields a headhunter posting."""
    from boss_agent.models import JobPosting, ScreeningPolicy

    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}
    mock_btn = MagicMock()
    mock_btn.rect = {"x": 50, "y": 50, "width": 100, "height": 50}
    mock_driver.find_elements.return_value = [mock_btn]

    mock_memory_mgr = MagicMock()
    mock_memory_mgr.load_memory.return_value = StructuredCandidateProfile(
        name="测试候选人",
        years_of_experience=7,
        core_skills=["Python", "Appium", "LLM"],
    )

    mock_matching_svc = MagicMock()
    mock_matching_svc.evaluate_and_draft_greeting.return_value = MatchGreetingResult(
        match_score=95,
        match_reasons=["技术栈高度匹配"],
        greeting_message="您好！看到贵司大模型平台岗位，我有完整实战经验……",
    )

    harness = SmokeHarness(
        driver=mock_driver,
        takeover_handler=TakeoverHandler(mock_driver, auto_confirm_for_test=True),
        memory_manager=mock_memory_mgr,
        matching_service=mock_matching_svc,
        screening_policy=ScreeningPolicy(
            channel_preference=channel_policy,
            title_whitelist=whitelist or [],
        ),
        preview_timeout_sec=0.01,
        enable_greeting_draft=True,
    )

    # Detail extraction on real devices yields no recruiter facet; inject a
    # headhunter posting directly to exercise the App-Enforced Filter path.
    harness.detail_page = MagicMock()
    harness.detail_page.extract_job_posting.return_value = JobPosting(
        title="大模型平台负责人",
        company_name="某人力资源服务公司",
        salary_range="40-60K",
        job_description=(
            "主导企业级大模型应用与Agent工作流平台建设，负责推理链编排、"
            "向量检索体系优化以及多智能体协同框架的架构设计。"
        ),
        recruiter_name="钟先生",
        recruiter_title="猎头顾问",
        is_headhunter=True,
    )
    return harness, mock_matching_svc


def test_smoke_harness_app_rule_violation_short_circuits_greeting():
    """direct_only + headhunter posting without whitelist rescue: the CLI workflow
    must NOT draft/type a greeting or open chat (issue review: workflows guard)."""
    harness, mock_matching_svc = _headhunter_smoke_harness("direct_only")

    with patch("time.sleep", return_value=None):
        harness.run_smoke_test()

    mock_matching_svc.evaluate_and_draft_greeting.assert_not_called()
    mock_matching_svc.render_match_card.assert_not_called()
    harness.detail_page.open_chat.assert_not_called()


def test_smoke_harness_relaxed_headhunter_still_drafts_greeting():
    """direct_only + headhunter posting hitting the whitelist: relaxation rescue keeps
    the normal greeting draft path alive."""
    harness, mock_matching_svc = _headhunter_smoke_harness("direct_only", whitelist=["大模型"])

    with patch("time.sleep", return_value=None):
        harness.run_smoke_test()

    mock_matching_svc.evaluate_and_draft_greeting.assert_called_once()
