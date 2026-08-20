"""
tests.unit.test_chat_page
=========================
Unit tests for ChatPage UI automation ensuring greeting typing without sending.
"""

from unittest.mock import MagicMock

from boss_agent.pages import ChatPage, JobDetailPage


def test_chat_page_open_chat_clicks_entry():
    driver = MagicMock()
    mock_btn = MagicMock()
    mock_btn.is_displayed.return_value = True

    chat_page = ChatPage(driver)
    chat_page.find_by_key = MagicMock(return_value=mock_btn)  # type: ignore[method-assign]

    success = chat_page.open_chat(timeout_sec=2.0)
    assert success is True
    chat_page.find_by_key.assert_called_with("chat.chat_entry_btn", timeout_sec=2.0)


def test_chat_page_type_greeting_without_sending():
    driver = MagicMock()
    mock_input = MagicMock()
    mock_input.is_displayed.return_value = True

    chat_page = ChatPage(driver)
    chat_page.find_by_key = MagicMock(return_value=mock_input)  # type: ignore[method-assign]
    chat_page.gestures.human_type = MagicMock()  # type: ignore[method-assign]

    greeting = "您好！我对该职位非常感兴趣，具备相关开发经验。"
    success = chat_page.type_greeting_message(greeting, timeout_sec=2.0)

    assert success is True
    chat_page.find_by_key.assert_called_with("chat.message_input", timeout_sec=2.0)
    chat_page.gestures.human_type.assert_called_with(mock_input, greeting)


def test_chat_page_navigate_back():
    driver = MagicMock()
    mock_back = MagicMock()

    chat_page = ChatPage(driver)
    chat_page.find_by_key = MagicMock(return_value=mock_back)  # type: ignore[method-assign]

    success = chat_page.navigate_back(timeout_sec=2.0)
    assert success is True
    chat_page.find_by_key.assert_called_with("chat.back_btn", timeout_sec=2.0)


def test_job_detail_page_open_chat_delegate():
    driver = MagicMock()
    mock_btn = MagicMock()

    detail_page = JobDetailPage(driver)
    detail_page.find_by_key = MagicMock(return_value=mock_btn)  # type: ignore[method-assign]

    success = detail_page.open_chat(timeout_sec=2.0)
    assert success is True
    detail_page.find_by_key.assert_called_with("chat.chat_entry_btn", timeout_sec=2.0)
