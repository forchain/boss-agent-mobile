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


def test_navigate_to_home_from_subpage_with_back_button():
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_back_btn = MagicMock()
    mock_back_btn.rect = {"x": 50, "y": 100, "width": 60, "height": 60}

    mock_search_icon = MagicMock()
    mock_search_icon.rect = {"x": 900, "y": 100, "width": 80, "height": 80}

    mock_job_tab = MagicMock()
    mock_job_tab.rect = {"x": 100, "y": 2300, "width": 200, "height": 100}

    step = 0

    def mock_find_elements(by, value):
        nonlocal step
        if "iv_back" in value or "返回" in value:
            # First 2 checks find back button
            if step < 2:
                return [mock_back_btn]
            return []
        if "search" in value or "ly_menu" in value:
            # After clicking back twice, search icon appears on home
            if step >= 2:
                return [mock_search_icon]
            return []
        if "tv_tab_1" in value or "职位" in value:
            return [mock_job_tab]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements

    page = JobListPage(mock_driver)

    def mock_click(elem):
        nonlocal step
        if elem == mock_back_btn:
            step += 1

    page.gestures.human_click = mock_click

    assert page.navigate_to_home(max_attempts=5) is True
    assert step >= 2


def test_navigate_to_home_from_other_tab():
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_search_icon = MagicMock()
    mock_search_icon.rect = {"x": 900, "y": 100, "width": 80, "height": 80}

    mock_job_tab = MagicMock()
    mock_job_tab.rect = {"x": 100, "y": 2300, "width": 200, "height": 100}

    on_job_tab = False

    def mock_find_elements(by, value):
        if "search" in value or "ly_menu" in value:
            return [mock_search_icon] if on_job_tab else []
        if "tv_tab_1" in value or "职位" in value:
            return [mock_job_tab]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements

    page = JobListPage(mock_driver)

    def mock_click(elem):
        nonlocal on_job_tab
        if elem == mock_job_tab:
            on_job_tab = True

    page.gestures.human_click = mock_click

    assert page.navigate_to_home(max_attempts=3) is True
    assert on_job_tab is True


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
