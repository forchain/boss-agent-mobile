"""Unit tests for home page state detection and navigation recovery."""

from unittest.mock import MagicMock

from boss_agent.models import JobPosting, SavedSearch
from boss_agent.pages import JobListPage
from boss_agent.workflows import SmokeHarness, TakeoverHandler


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


def test_navigate_to_home_gives_up_after_bounded_attempts():
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}
    mock_driver.find_elements.return_value = []

    page = JobListPage(mock_driver)

    assert page.navigate_to_home(max_attempts=4) is False
    assert mock_driver.press_keycode.call_count <= 4


def test_smoke_harness_recovers_to_home_before_search():
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_btn = MagicMock()
    mock_btn.rect = {"x": 50, "y": 50, "width": 100, "height": 50}

    mock_title_elem = MagicMock()
    mock_title_elem.text = "大模型 Agent 架构师"

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
        saved_search=SavedSearch(id="test_query", name="Test Query"),
    )

    job = harness.run_smoke_test()
    assert isinstance(job, JobPosting)
    assert job.title == "大模型 Agent 架构师"


def test_chat_screen_does_not_collide_with_search_or_home():
    from boss_agent.pages import SearchPage

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
