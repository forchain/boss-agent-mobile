"""
tests/unit/test_home_navigation_recovery.py
===========================================
Home page state detection, Back-only recovery, and where a feed run starts.

``JobListPage`` still owns home recovery — the pipeline drives it, it does not own it — so
the page-level guarantees below (Back-only, the 职位 tab anchor, bounded attempts) are
tested against that object, with the device findings they encode kept in their docstrings.

The last test in this file used to be different in kind: it drove ``SmokeHarness`` over a
mocked driver to show that a run recovered to home before searching, which asserted the
shape of a procedural walk (issue #391). That decision belongs to ``JobFeedPipeline`` now —
the run that opens with no keyword says ``enable_search=False`` and the pipeline resets to
home — so it is asserted there, on the config the run was actually given.
"""

from unittest.mock import MagicMock

import pytest
from _feed_harness import ScriptedFeed, _card, _detail_page, _pipeline, _posting

from boss_agent.feed_pipeline import FeedStreamConfig
from boss_agent.pages.job_feed import JobListPage


def test_is_on_home_page_detection():
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_search_icon = MagicMock()
    mock_search_icon.rect = {"x": 900, "y": 100, "width": 80, "height": 80}

    # Case 1: Search icon is present -> On Home Page
    mock_driver.find_elements.return_value = [mock_search_icon]
    page = JobListPage(mock_driver)
    assert page.is_on_home_page() is True

    # Case 2: No search icon or job card -> Not on Home Page
    mock_driver.find_elements.return_value = []
    assert page.is_on_home_page() is False


def test_navigate_to_home_presses_back_until_search_entry_visible():
    """Home recovery is now a fast Back-only loop: no probing of unrelated buttons."""
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_search_icon = MagicMock()
    mock_search_icon.rect = {"x": 900, "y": 100, "width": 80, "height": 80}

    back_presses = 0
    queried_values: list[str] = []

    def mock_find_elements(by, value):
        queried_values.append(value)
        if "ly_menu" in value or "img_icon" in value:
            return [mock_search_icon] if back_presses >= 2 else []
        return []

    def mock_press_keycode(code):
        nonlocal back_presses
        back_presses += 1

    mock_driver.find_elements.side_effect = mock_find_elements
    mock_driver.press_keycode.side_effect = mock_press_keycode

    page = JobListPage(mock_driver)
    clicked: list = []
    page.gestures.human_click = clicked.append

    assert page.navigate_to_home() is True
    assert back_presses == 2
    assert clicked == [], "recovery must not click unrelated close/back buttons"
    banned = ("btn_cancel", "iv_close", "btn_back", "iv_back", "返回")
    assert not any(b in v for v in queried_values for b in banned), (
        f"recovery must not scan irrelevant subpage buttons, queried: {queried_values}"
    )


def test_navigate_to_home_recovers_from_another_bottom_tab():
    """Device-verified on emulator-5554: hardware Back does NOT leave the 消息/我的 tab
    (two presses left us outside the home anchor); clicking the 职位 tab does."""
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_search_icon = MagicMock()
    mock_search_icon.rect = {"x": 900, "y": 100, "width": 80, "height": 80}
    mock_job_tab = MagicMock()
    mock_job_tab.rect = {"x": 100, "y": 2300, "width": 200, "height": 100}

    state = {"on_job_tab": False}

    def mock_find_elements(by, value):
        if "ly_menu" in value or "img_icon" in value:
            return [mock_search_icon] if state["on_job_tab"] else []
        if "tv_tab_1" in value or "职位" in value:
            return [] if state["on_job_tab"] else [mock_job_tab]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements

    page = JobListPage(mock_driver)

    def mock_click(elem):
        if elem is mock_job_tab:
            state["on_job_tab"] = True

    page.gestures.human_click = mock_click

    assert page.navigate_to_home() is True
    assert state["on_job_tab"] is True
    assert mock_driver.press_keycode.call_count == 0, (
        "another bottom tab ignores Back; the 职位 tab anchor is the recovery there"
    )


def test_navigate_to_home_gives_up_after_bounded_attempts():
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}
    mock_driver.find_elements.return_value = []

    page = JobListPage(mock_driver)

    assert page.navigate_to_home(max_attempts=4) is False
    assert mock_driver.press_keycode.call_count <= 4


@pytest.mark.asyncio
async def test_a_run_with_no_keyword_resets_to_home_before_browsing_recommendations():
    """A keyword-less run starts from the home feed, and does not go looking for a search.

    ``SearchConfig.should_search`` is False when no keyword was given, which the pipeline
    reads as ``enable_search=False``: reset to home, browse what is recommended, submit no
    search. The old version of this test proved the same thing by walking a harness over a
    mocked driver; here the claim is about the run's intent and where the engine started
    it, and the posting it extracts comes from the scripted feed rather than from whatever
    a mocked ``find_elements`` happened to return.
    """
    feed = ScriptedFeed([[_card("大模型 Agent 架构师", "智元创新")]])
    pipeline = _pipeline(None, feed=feed, detail=_detail_page(posting=_posting()))

    result = await pipeline.stream_jobs(
        FeedStreamConfig(keyword=None, enable_search=False, max_jobs=1)
    )

    assert feed.home_visits == 1, "a keyword-less run must recover to the home feed first"
    assert pipeline.search_page.search.call_count == 0, "there is no keyword to search for"
    assert [p.title for p in result.postings] == ["AI Agent 平台工程师"]


def test_chat_screen_does_not_collide_with_search_or_home():
    from boss_agent.pages.job_feed import SearchPage

    mock_driver = MagicMock()
    mock_chat_input = MagicMock()
    mock_chat_input.rect = {"x": 100, "y": 2000, "width": 800, "height": 100}

    # When on chat screen with chat_editor visible
    def mock_find_elements(by, value):
        if "chat_editor" in value or "et_sendmessage" in value or "et_message" in value:
            return [mock_chat_input]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements

    job_list_page = JobListPage(mock_driver)
    search_page = SearchPage(mock_driver)

    # Must NOT be identified as home page
    assert job_list_page.is_on_home_page() is False
    # Must NOT be identified as search page
    assert search_page.is_search_page() is False
