"""Unit tests for IndustryFilterDialogPage, FilterConfig industries multi-select, and SmokeHarness integration."""

from unittest.mock import MagicMock

from boss_agent.models import FilterConfig, JobPosting, SearchConfig
from boss_agent.pages import IndustryFilterDialogPage
from boss_agent.workflows import SmokeHarness, TakeoverHandler


def test_industry_filter_config():
    # Empty industries
    cfg_empty = FilterConfig(
        education=None,
        salary=None,
        experience=None,
        activity=None,
        company_scales=[],
        industries=[],
    )
    assert cfg_empty.has_industry_filters is False
    assert cfg_empty.has_filters is False

    # Config with multiple industries
    cfg = FilterConfig(
        education=None,
        salary=None,
        experience=None,
        activity=None,
        company_scales=[],
        industries=["游戏", "人工智能", "半导体/芯片"],
    )
    assert cfg.has_industry_filters is True
    assert cfg.has_filters is True
    assert len(cfg.industries) == 3


def test_industry_filter_dialog_page_interactions():
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_industry_entry_btn = MagicMock()
    mock_industry_entry_btn.rect = {"x": 600, "y": 100, "width": 80, "height": 40}

    mock_confirm_btn = MagicMock()
    mock_confirm_btn.rect = {"x": 550, "y": 1750, "width": 450, "height": 80}

    mock_cancel_btn = MagicMock()
    mock_cancel_btn.rect = {"x": 50, "y": 1750, "width": 450, "height": 80}

    mock_option_elem = MagicMock()
    mock_option_elem.rect = {"x": 300, "y": 500, "width": 200, "height": 60}

    def mock_find_elements(by, value):
        if "btn_confirm" in value or "确定" in value:
            return [mock_confirm_btn]
        if "btn_cancel" in value or "取消" in value:
            return [mock_cancel_btn]
        if "行业" in value:
            return [mock_industry_entry_btn]
        if any(opt in value for opt in ["游戏", "人工智能", "半导体/芯片", "电子商务"]):
            return [mock_option_elem]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements

    page = IndustryFilterDialogPage(mock_driver)

    # 1. Open industry filter
    assert page.open_industry_filter() is True
    assert page.is_dialog_open() is True

    # 2. Select single industry
    assert page.select_industry_option("游戏") is True

    # 3. Select multiple industries (multi-select)
    selected = page.select_industries(["游戏", "人工智能", "半导体/芯片"])
    assert selected == ["游戏", "人工智能", "半导体/芯片"]

    # 4. Cancel filter
    assert page.cancel_filter() is True

    # 5. Apply industry filters workflow
    assert page.apply_industry_filters(["游戏", "人工智能"]) is True


def test_industry_filter_dialog_auto_scroll():
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_confirm_btn = MagicMock()
    mock_confirm_btn.rect = {"x": 550, "y": 1750, "width": 450, "height": 80}

    mock_scrolled_option = MagicMock()
    mock_scrolled_option.rect = {"x": 200, "y": 800, "width": 200, "height": 60}

    scroll_count = 0

    def mock_find_elements(by, value):
        nonlocal scroll_count
        if "btn_confirm" in value or "确定" in value:
            return [mock_confirm_btn]
        if "半导体/芯片" in value:
            # Only found after at least 1 scroll
            if scroll_count >= 1:
                return [mock_scrolled_option]
            return []
        return []

    mock_driver.find_elements.side_effect = mock_find_elements

    page = IndustryFilterDialogPage(mock_driver)

    def mock_swipe(start, end, duration_ms=400):
        nonlocal scroll_count
        scroll_count += 1

    page.gestures.human_swipe = mock_swipe

    # Should find after auto scroll
    assert page.select_industry_option("半导体/芯片", auto_scroll=True) is True
    assert scroll_count >= 1


def test_smoke_harness_with_industry_filter():
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_btn = MagicMock()
    mock_btn.rect = {"x": 50, "y": 50, "width": 100, "height": 50}

    mock_title_elem = MagicMock()
    mock_title_elem.text = "资深大模型算法专家"

    def mock_find_elements(by, value):
        if "tv_job_name" in value:
            return [mock_title_elem]
        return [mock_btn]

    mock_driver.find_elements.side_effect = mock_find_elements

    takeover = TakeoverHandler(mock_driver, auto_confirm_for_test=True)
    harness = SmokeHarness(
        driver=mock_driver,
        takeover_handler=takeover,
        search_config=SearchConfig(keyword="agent"),
        filter_config=FilterConfig(
            education="硕士",
            salary="5万元以上",
            experience="10年以上",
            activity="今日活跃",
            company_scales=["100-499人"],
            industries=["游戏", "人工智能"],
        ),
    )

    job = harness.run_smoke_test()
    assert isinstance(job, JobPosting)
    assert job.title == "资深大模型算法专家"
