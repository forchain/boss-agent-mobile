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


def test_chat_page_click_send_success():
    driver = MagicMock()
    mock_btn = MagicMock()

    chat_page = ChatPage(driver)
    chat_page.find_by_key = MagicMock(return_value=mock_btn)  # type: ignore[method-assign]
    chat_page.gestures.human_click = MagicMock()  # type: ignore[method-assign]

    success = chat_page.click_send(timeout_sec=2.0)
    assert success is True
    chat_page.find_by_key.assert_called_with("chat.send_btn", timeout_sec=2.0)
    chat_page.gestures.human_click.assert_called_with(mock_btn)


def test_chat_page_click_send_not_found():
    driver = MagicMock()

    chat_page = ChatPage(driver)
    chat_page.find_by_key = MagicMock(return_value=None)  # type: ignore[method-assign]

    success = chat_page.click_send(timeout_sec=2.0)
    assert success is False
    chat_page.find_by_key.assert_called_with("chat.send_btn", timeout_sec=2.0)


def test_chat_page_send_message_flow():
    driver = MagicMock()

    chat_page = ChatPage(driver)
    chat_page.type_greeting_message = MagicMock(return_value=True)  # type: ignore[method-assign]
    chat_page.click_send = MagicMock(return_value=True)  # type: ignore[method-assign]

    assert chat_page.send_message("收到 谢谢", timeout_sec=3.0) is True
    chat_page.type_greeting_message.assert_called_once_with("收到 谢谢", timeout_sec=3.0, clear_first=True)
    chat_page.click_send.assert_called_once_with(timeout_sec=3.0)


def test_chat_page_send_message_empty():
    driver = MagicMock()
    chat_page = ChatPage(driver)
    assert chat_page.send_message("") is False
    assert chat_page.send_message("   ") is False


def test_chat_page_clear_message_input():
    driver = MagicMock()
    mock_input = MagicMock()

    chat_page = ChatPage(driver)
    chat_page.find_by_key = MagicMock(return_value=mock_input)  # type: ignore[method-assign]

    assert chat_page.clear_message_input(timeout_sec=2.0) is True
    chat_page.find_by_key.assert_called_with("chat.message_input", timeout_sec=2.0)
    mock_input.clear.assert_called_once()

