"""Unit tests for FilterConfig and FilterDialogPage."""

from unittest.mock import MagicMock

from boss_agent.models import FilterConfig
from boss_agent.pages import FilterDialogPage


def test_filter_config_defaults_and_validation():
    cfg = FilterConfig()
    assert cfg.education == "硕士"
    assert cfg.salary == "5万元以上"
    assert cfg.experience == "10年以上"
    assert cfg.activity == "今日活跃"
    assert len(cfg.company_scales) == 4
    assert "100-499人" in cfg.company_scales
    assert "10000人以上" in cfg.company_scales
    assert cfg.has_filters is True

    # Empty filter config
    cfg_empty = FilterConfig(
        education=None,
        salary=None,
        experience=None,
        activity=None,
        company_scales=[],
    )
    assert cfg_empty.has_filters is False


def test_filter_dialog_page_interactions():
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_filter_btn = MagicMock()
    mock_filter_btn.rect = {"x": 500, "y": 100, "width": 80, "height": 40}

    mock_confirm_btn = MagicMock()
    mock_confirm_btn.rect = {"x": 400, "y": 1750, "width": 600, "height": 80}

    mock_reset_btn = MagicMock()
    mock_reset_btn.rect = {"x": 50, "y": 1750, "width": 300, "height": 80}

    mock_option_elem = MagicMock()
    mock_option_elem.rect = {"x": 300, "y": 500, "width": 200, "height": 60}

    def mock_find_elements(by, value):
        if "btn_confirm" in value or "确定" in value:
            return [mock_confirm_btn]
        if "btn_reset" in value or "清除" in value:
            return [mock_reset_btn]
        if "筛选" in value:
            return [mock_filter_btn]
        if any(opt in value for opt in ["硕士", "5万元以上", "10年以上", "今日活跃", "100-499人"]):
            return [mock_option_elem]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements

    page = FilterDialogPage(mock_driver)

    # Open filter
    assert page.open_filter() is True

    # Check is open
    assert page.is_dialog_open() is True

    # Select single option
    assert page.select_option("硕士") is True

    # Apply full filter configuration
    cfg = FilterConfig(
        education="硕士",
        salary="5万元以上",
        experience="10年以上",
        activity="今日活跃",
        company_scales=["100-499人"],
    )
    assert page.apply_filters(cfg) is True


def test_smoke_harness_with_filter_config():
    from boss_agent.models import JobPosting, SearchConfig
    from boss_agent.workflows import SmokeHarness, TakeoverHandler

    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_btn = MagicMock()
    mock_btn.rect = {"x": 50, "y": 50, "width": 100, "height": 50}

    mock_title_elem = MagicMock()
    mock_title_elem.text = "资深 Agent 架构师"

    def mock_find_elements(by, value):
        if "tv_job_name" in value:
            return [mock_title_elem]
        if "chat" in value or "editText_with_scrollbar" in value or "btn_chat" in value:
            return []
        return [mock_btn]

    mock_driver.find_elements.side_effect = mock_find_elements

    takeover = TakeoverHandler(mock_driver, auto_confirm_for_test=True)
    harness = SmokeHarness(
        driver=mock_driver,
        takeover_handler=takeover,
        search_config=SearchConfig(keyword="agent"),
        filter_config=FilterConfig(),
    )

    job = harness.run_smoke_test()
    assert isinstance(job, JobPosting)
