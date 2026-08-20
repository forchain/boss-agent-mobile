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
    mock_desc_elem.text = "负责移动端自动化与大模型结合研发。"

    def mock_find_elements(by, value):
        if "tv_job_name" in value:
            return [mock_title_elem]
        if "tv_company_name" in value:
            return [mock_company_elem]
        if "tv_job_salary" in value:
            return [mock_salary_elem]
        if "tv_job_desc" in value:
            return [mock_desc_elem]
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
