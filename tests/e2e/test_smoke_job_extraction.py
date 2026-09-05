"""
tests/e2e/test_smoke_job_extraction.py
======================================
Integration smoke test verifying end-to-end job detail extraction (Criterion 4).
"""

from unittest.mock import MagicMock, patch

from boss_agent.models import JobPosting
from boss_agent.workflows import SmokeHarness, TakeoverHandler


def test_smoke_harness_end_to_end_job_detail_extraction():
    """Verify that SmokeHarness executes the full lifecycle and parses a JobPosting."""
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    # Mock page elements
    mock_startup_elem = MagicMock()
    mock_startup_elem.rect = {"x": 200, "y": 1800, "width": 680, "height": 100}

    mock_job_card = MagicMock()
    mock_job_card.rect = {"x": 50, "y": 300, "width": 980, "height": 220}

    mock_title_elem = MagicMock()
    mock_title_elem.text = "资深 Python / Android 自动化专家"

    mock_company_elem = MagicMock()
    mock_company_elem.text = "北京智联前沿科技有限公司"

    mock_salary_elem = MagicMock()
    mock_salary_elem.text = "40-65K·16薪"

    mock_desc_elem = MagicMock()
    mock_desc_elem.text = (
        "岗位职责：\n"
        "1. 负责大规模移动端自动化框架设计与高可靠执行引擎开发；\n"
        "2. 深度优化反爬风控拟真轨迹与验证码智能接管策略；\n"
        "3. 具备 5 年以上 Python / Android SDK / Appium 深度实战经验。"
    )
    mock_search_icon = MagicMock()
    mock_search_icon.rect = {"x": 950, "y": 100, "width": 80, "height": 80}

    def mock_find_elements(by, value):
        if "同意" in value or "好的" in value:
            return [mock_startup_elem]
        if "ly_menu" in value or "search" in value:
            return [mock_search_icon]
        if "tv_job_name" in value or "job_title" in value:
            return [mock_title_elem]
        if "tv_company_name" in value or "company_name" in value:
            return [mock_company_elem]
        if "tv_job_salary" in value or "salary" in value:
            return [mock_salary_elem]
        if "tv_job_desc" in value or "job_description" in value:
            return [mock_desc_elem]
        if "job_name" in value or "tv_position_name" in value:
            return [mock_job_card]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements

    takeover = TakeoverHandler(mock_driver, auto_confirm_for_test=True)
    from boss_agent.models import FilterConfig, SearchConfig

    harness = SmokeHarness(
        driver=mock_driver,
        takeover_handler=takeover,
        search_config=SearchConfig(keyword=None),
        filter_config=FilterConfig(
            education=None, salary=None, experience=None, activity=None, company_scales=[]
        ),
        enable_greeting_draft=False,
    )

    with patch("time.sleep", return_value=None):
        job_posting = harness.run_smoke_test()

    # Assert Criterion 4 specifications
    assert isinstance(job_posting, JobPosting)
    assert job_posting.title == "资深 Python / Android 自动化专家"
    assert job_posting.company_name == "北京智联前沿科技有限公司"
    assert job_posting.salary_range == "40-65K·16薪"
    assert len(job_posting.job_description) >= 20
    assert "岗位职责" in job_posting.job_description


def test_smoke_harness_with_search_enabled():
    """Verify that SmokeHarness executes search flow when search keyword is configured."""
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_search_icon = MagicMock()
    mock_search_icon.rect = {"x": 950, "y": 100, "width": 80, "height": 80}

    mock_input_elem = MagicMock()
    mock_input_elem.rect = {"x": 150, "y": 120, "width": 700, "height": 60}

    mock_submit_elem = MagicMock()
    mock_submit_elem.rect = {"x": 900, "y": 200, "width": 100, "height": 60}

    mock_job_card = MagicMock()
    mock_job_card.rect = {"x": 50, "y": 300, "width": 980, "height": 220}

    mock_title_elem = MagicMock()
    mock_title_elem.text = "Agent 开发工程师"

    mock_btn = MagicMock()
    mock_btn.rect = {"x": 500, "y": 100, "width": 80, "height": 40}

    def mock_find_elements(by, value):
        if "et_search" in value or "EditText" in value:
            return [mock_input_elem]
        if "tv_search" in value or "搜索" in value:
            return [mock_submit_elem]
        if "ly_menu" in value or "search_icon" in value:
            return [mock_search_icon]
        if "tv_job_name" in value:
            return [mock_title_elem]
        if "job_name" in value or "tv_position_name" in value or "cl_card_container" in value:
            return [mock_job_card]
        if "btn_confirm" in value or "确定" in value or "筛选" in value:
            return [mock_btn]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements

    takeover = TakeoverHandler(mock_driver, auto_confirm_for_test=True)
    harness = SmokeHarness(
        driver=mock_driver,
        takeover_handler=takeover,
        search_config=None,  # default is SearchConfig(keyword="agent")
        filter_config=None,
        enable_greeting_draft=False,
    )

    with patch("time.sleep", return_value=None):
        job = harness.run_smoke_test()
    assert isinstance(job, JobPosting)
    mock_input_elem.send_keys.assert_called()


def test_smoke_harness_with_search_disabled():
    """Verify that SmokeHarness skips search when keyword is None."""
    from boss_agent.models import FilterConfig, SearchConfig

    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_input_elem = MagicMock()
    mock_job_card = MagicMock()
    mock_job_card.rect = {"x": 50, "y": 300, "width": 980, "height": 220}
    mock_title_elem = MagicMock()
    mock_search_icon = MagicMock()
    mock_search_icon.rect = {"x": 950, "y": 100, "width": 80, "height": 80}

    def mock_find_elements(by, value):
        if "ly_menu" in value or "search" in value:
            return [mock_search_icon]
        if "et_search" in value:
            return [mock_input_elem]
        if "job_name" in value or "tv_position_name" in value or "cl_card_container" in value:
            return [mock_job_card]
        if "tv_job_name" in value:
            return [mock_title_elem]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements

    takeover = TakeoverHandler(mock_driver, auto_confirm_for_test=True)
    harness = SmokeHarness(
        driver=mock_driver,
        takeover_handler=takeover,
        search_config=SearchConfig(keyword=None),
        filter_config=FilterConfig(
            education=None, salary=None, experience=None, activity=None, company_scales=[]
        ),
        enable_greeting_draft=False,
    )

    with patch("time.sleep", return_value=None):
        job = harness.run_smoke_test()
    assert isinstance(job, JobPosting)
    mock_input_elem.send_keys.assert_not_called()
