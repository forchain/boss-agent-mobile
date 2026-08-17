"""
tests/e2e/test_smoke_job_extraction.py
======================================
Integration smoke test verifying end-to-end job detail extraction (Criterion 4).
"""

from unittest.mock import MagicMock

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

    def mock_find_elements(by, value):
        if "同意" in value or "好的" in value:
            return [mock_startup_elem]
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
    harness = SmokeHarness(driver=mock_driver, takeover_handler=takeover)

    job_posting = harness.run_smoke_test()

    # Assert Criterion 4 specifications
    assert isinstance(job_posting, JobPosting)
    assert job_posting.title == "资深 Python / Android 自动化专家"
    assert job_posting.company_name == "北京智联前沿科技有限公司"
    assert job_posting.salary_range == "40-65K·16薪"
    assert len(job_posting.job_description) >= 20
    assert "岗位职责" in job_posting.job_description
