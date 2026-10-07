"""
tests/unit/test_industry_filter_dialog.py
=========================================
Industry multi-select in ``FilterConfig``, the industry dialog page, and how a run applies it.

The integration test at the bottom of this file used to drive ``SmokeHarness`` over a
mocked driver to show that a run with industries configured still completed — asserting the
shape of a procedural walk rather than anything about the industries (issue #391). The
industry multi-select belongs to ``JobFeedPipeline`` now, so that claim is made there: the
run passes the operator's industry list to the industry dialog, and keeps its extraction.

The dialog's own behaviours — multi-select, cancel, auto-scroll to an off-screen option —
stay tested against ``IndustryFilterDialogPage``, which is the object the pipeline drives.
"""

from unittest.mock import MagicMock, patch

import pytest
from _feed_harness import ScriptedFeed, _card, _detail_page, _pipeline, _posting

from boss_agent.feed_pipeline import FeedStreamConfig
from boss_agent.pages import IndustryFilterDialogPage
from boss_agent.search_entities import FilterConfig

WITH_INDUSTRIES = FilterConfig(
    education="硕士",
    salary="5万元以上",
    experience="10年以上",
    activity="今日活跃",
    company_scales=["100-499人"],
    industries=["游戏", "人工智能"],
)


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


@pytest.mark.asyncio
async def test_a_run_applies_the_operator_industry_list_to_the_industry_dialog():
    """Industries are a multi-select the run hands over whole, not one at a time.

    ``IndustryFilterDialogPage`` is not a native app filter — it is the engine's own
    screen — and the pipeline applies it before the general filter dialog, which is the
    order the app expects. The list that arrives has to be the operator's own: an industry
    silently dropped here is a whole category of postings the run never shows them.
    """
    pipeline = _pipeline(
        None,
        feed=ScriptedFeed([[_card("资深大模型算法专家", "智元创新")]]),
        detail=_detail_page(posting=_posting()),
    )

    with patch("boss_agent.feed_pipeline.IndustryFilterDialogPage") as industry_cls:
        result = await pipeline.stream_jobs(
            FeedStreamConfig(keyword="Agent", max_jobs=1, filter_config=WITH_INDUSTRIES)
        )

    industry_cls.return_value.apply_industry_filters.assert_called_once()
    industries = industry_cls.return_value.apply_industry_filters.call_args.args[0]
    assert industries == ["游戏", "人工智能"]
    # Filtering narrows what the feed shows; it is not what decides whether the run
    # produced anything, so the extraction is still reported.
    assert [p.title for p in result.postings] == ["AI Agent 平台工程师"]
