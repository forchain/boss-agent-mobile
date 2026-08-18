"""Unit tests for SearchConfig, SearchPage, and JobListPage search navigation."""

from unittest.mock import MagicMock

from boss_agent.models import SearchConfig
from boss_agent.pages import JobListPage, SearchPage


def test_search_config_default_and_validation():
    # Default is 'agent'
    cfg = SearchConfig()
    assert cfg.keyword == "agent"
    assert cfg.should_search is True

    # Custom keyword
    cfg_custom = SearchConfig(keyword="Android Architect")
    assert cfg_custom.keyword == "Android Architect"
    assert cfg_custom.should_search is True

    # None / Empty string disables search
    cfg_none = SearchConfig(keyword=None)
    assert cfg_none.keyword is None
    assert cfg_none.should_search is False

    cfg_empty = SearchConfig(keyword="   ")
    assert cfg_empty.should_search is False


def test_job_list_page_open_search_and_tab_navigation():
    mock_driver = MagicMock()
    mock_search_icon = MagicMock()
    mock_search_icon.rect = {"x": 950, "y": 100, "width": 80, "height": 80}
    mock_tab_elem = MagicMock()
    mock_tab_elem.rect = {"x": 100, "y": 1800, "width": 80, "height": 40}

    def mock_find_elements(by, value):
        if "ly_menu" in value or "search" in value.lower():
            return [mock_search_icon]
        if "tv_tab_1" in value or "职位" in value:
            return [mock_tab_elem]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements

    list_page = JobListPage(mock_driver)

    # Test ensuring on Job tab
    assert list_page.ensure_job_tab() is True

    # Test open_search
    assert list_page.open_search() is True


def test_search_page_input_and_submission():
    mock_driver = MagicMock()
    mock_input_elem = MagicMock()
    mock_input_elem.rect = {"x": 150, "y": 120, "width": 700, "height": 60}
    mock_submit_elem = MagicMock()
    mock_submit_elem.rect = {"x": 900, "y": 200, "width": 100, "height": 60}
    mock_clear_elem = MagicMock()
    mock_clear_elem.rect = {"x": 960, "y": 100, "width": 50, "height": 50}
    mock_back_elem = MagicMock()
    mock_back_elem.rect = {"x": 80, "y": 120, "width": 50, "height": 50}

    def mock_find_elements(by, value):
        if "et_search" in value or "EditText" in value:
            return [mock_input_elem]
        if "tv_search" in value or "搜索" in value:
            return [mock_submit_elem]
        if "iv_clear" in value:
            return [mock_clear_elem]
        if "iv_back" in value or "返回" in value:
            return [mock_back_elem]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements

    search_page = SearchPage(mock_driver)
    assert search_page.is_search_page() is True

    # Perform keyword search
    assert search_page.search("agent") is True
    mock_input_elem.send_keys.assert_called()

    # Perform clear
    assert search_page.clear_input() is True

    # Perform back
    assert search_page.navigate_back() is True
