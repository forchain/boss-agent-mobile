"""
tests.unit.test_chat_inbox_page
===============================
Unit tests for the New Greeting Inbox page object, the chat disinterest
sequence, and the new inbox locator configuration (Issue #206).
"""

from unittest.mock import MagicMock

import pytest

from boss_agent.pages import ChatInboxMessage, ChatInboxPage, ChatPage
from boss_agent.rejection import DISINTEREST_REASON
from droid_agent_core.locators import LocatorRegistry

NEW_LOCATOR_KEYS = (
    "chat_inbox.entry_tab",
    "chat_inbox.new_greeting_tab",
    "chat_inbox.message_card",
    "chat_inbox.sender_name",
    "chat_inbox.message_text",
    "chat.disinterest_btn",
    "chat.disinterest_reason_option",
)


@pytest.fixture
def registry():
    return LocatorRegistry()


def test_new_inbox_and_disinterest_locators_are_configured(registry):
    for key in NEW_LOCATOR_KEYS:
        assert registry.get_selectors(key), f"Missing locator configuration for '{key}'"


def test_disinterest_reason_locator_formats_the_standardized_reason(registry):
    selectors = registry.get_selectors(
        "chat.disinterest_reason_option", format_args={"reason": DISINTEREST_REASON}
    )
    assert selectors
    assert any(DISINTEREST_REASON in sel.value for sel in selectors)


def test_inbox_message_key_is_stable_and_sender_scoped():
    first = ChatInboxMessage(sender_name="李女士", message_text="抱歉，暂不匹配")
    same = ChatInboxMessage(sender_name="李女士", message_text="抱歉，暂不匹配")
    other_sender = ChatInboxMessage(sender_name="王先生", message_text="抱歉，暂不匹配")
    other_text = ChatInboxMessage(sender_name="李女士", message_text="方便约面试吗")

    assert first.key == same.key
    assert first.key != other_sender.key
    assert first.key != other_text.key


def test_is_on_inbox_detects_new_greeting_marker():
    driver = MagicMock()
    page = ChatInboxPage(driver)
    page.find_by_key = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]

    assert page.is_on_inbox(timeout_sec=0.1) is True
    page.find_by_key.assert_called_with("chat_inbox.new_greeting_tab", timeout_sec=0.1)


def test_open_inbox_clicks_message_tab_then_new_greeting_tab():
    driver = MagicMock()
    page = ChatInboxPage(driver)
    marker = MagicMock()
    page.find_by_key = MagicMock(return_value=marker)  # type: ignore[method-assign]
    page.gestures.human_click = MagicMock()  # type: ignore[method-assign]

    assert page.open_inbox(timeout_sec=0.1) is True

    clicked_keys = [call.args[0] for call in page.find_by_key.call_args_list]
    assert clicked_keys == ["chat_inbox.entry_tab", "chat_inbox.new_greeting_tab"]
    assert page.gestures.human_click.call_count == 2


def test_open_inbox_reports_failure_without_new_greeting_tab():
    driver = MagicMock()
    page = ChatInboxPage(driver)
    page.find_by_key = MagicMock(  # type: ignore[method-assign]
        side_effect=lambda key, **_: MagicMock() if key == "chat_inbox.entry_tab" else None
    )

    assert page.open_inbox(timeout_sec=0.1) is False


def _message_card(sender: str, text: str) -> MagicMock:
    """Build a fake inbox card whose sub-element lookups return sender/text nodes."""
    sender_node = MagicMock(text=sender)
    text_node = MagicMock(text=text)
    card = MagicMock(text=f"{sender}\n{text}")
    card.find_elements.side_effect = lambda by, value: (
        [sender_node] if "name" in value else [text_node]
    )
    return card


def _page_with_cards(cards: list[MagicMock]) -> ChatInboxPage:
    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}
    driver.find_elements.side_effect = lambda by, value: (
        cards if "tv_name" not in value and "tv_msg" not in value else cards
    )
    page = ChatInboxPage(driver)
    page._find_message_cards = MagicMock(return_value=cards)  # type: ignore[method-assign]
    return page


def test_extract_visible_messages_builds_sender_text_and_key():
    card = _message_card("李女士", "抱歉，您的经历与岗位要求不太匹配")
    page = _page_with_cards([card])

    messages = page.extract_visible_messages()

    assert len(messages) == 1
    assert messages[0].sender_name == "李女士"
    assert messages[0].message_text == "抱歉，您的经历与岗位要求不太匹配"
    assert messages[0].key
    assert messages[0].element is card


def test_extract_visible_messages_skips_cards_without_text():
    empty_card = MagicMock(text="")
    empty_card.find_elements.return_value = []
    good_card = _message_card("王先生", "岗位已招满，感谢关注")
    page = _page_with_cards([empty_card, good_card])

    messages = page.extract_visible_messages()

    assert [m.message_text for m in messages] == ["岗位已招满，感谢关注"]


def test_scroll_inbox_swipes_upward():
    page = _page_with_cards([])
    page.gestures.human_swipe = MagicMock()  # type: ignore[method-assign]
    page.gestures.random_sleep = MagicMock()  # type: ignore[method-assign]

    page.scroll_inbox()

    assert page.gestures.human_swipe.call_count == 1
    start, end = page.gestures.human_swipe.call_args.args[:2]
    assert start.y > end.y


def test_open_message_uses_humanized_click():
    card = _message_card("李女士", "暂不匹配")
    page = _page_with_cards([card])
    page.gestures.human_click = MagicMock()  # type: ignore[method-assign]
    message = ChatInboxMessage(sender_name="李女士", message_text="暂不匹配", element=card)

    assert page.open_message(message) is True
    page.gestures.human_click.assert_called_once_with(card)


def test_open_message_without_element_is_a_noop():
    page = _page_with_cards([])
    page.gestures.human_click = MagicMock()  # type: ignore[method-assign]

    assert page.open_message(ChatInboxMessage(sender_name="李女士", message_text="暂不匹配")) is False
    page.gestures.human_click.assert_not_called()


def test_mark_disinterest_clicks_button_then_standardized_reason():
    driver = MagicMock()
    page = ChatInboxPage(driver)
    button = MagicMock()
    option = MagicMock()
    page.find_by_key = MagicMock(side_effect=lambda key, **_: button if "btn" in key else option)
    page.gestures.human_click = MagicMock()  # type: ignore[method-assign]

    assert page.mark_disinterest(timeout_sec=0.1) is True

    assert page.gestures.human_click.call_args_list[0].args[0] is button
    assert page.gestures.human_click.call_args_list[1].args[0] is option
    reason_call = page.find_by_key.call_args_list[1]
    assert reason_call.args[0] == "chat.disinterest_reason_option"
    assert reason_call.kwargs["format_args"] == {"reason": DISINTEREST_REASON}


def test_mark_disinterest_fails_fast_when_reason_option_absent():
    """Never guess an alternative disinterest reason: a miss aborts the sequence."""
    driver = MagicMock()
    page = ChatInboxPage(driver)
    button = MagicMock()
    page.find_by_key = MagicMock(  # type: ignore[method-assign]
        side_effect=lambda key, **_: button if "btn" in key else None
    )
    page.gestures.human_click = MagicMock()  # type: ignore[method-assign]

    assert page.mark_disinterest(timeout_sec=0.1) is False
    page.gestures.human_click.assert_called_once_with(button)


def test_mark_disinterest_fails_when_button_absent():
    driver = MagicMock()
    page = ChatInboxPage(driver)
    page.find_by_key = MagicMock(return_value=None)  # type: ignore[method-assign]
    page.gestures.human_click = MagicMock()  # type: ignore[method-assign]

    assert page.mark_disinterest(timeout_sec=0.1) is False
    page.gestures.human_click.assert_not_called()


def test_chat_page_send_message_types_then_sends():
    driver = MagicMock()
    page = ChatPage(driver)
    input_box = MagicMock()
    send_button = MagicMock()
    page.find_by_key = MagicMock(side_effect=lambda key, **_: input_box if "input" in key else send_button)
    page.gestures.human_type = MagicMock()  # type: ignore[method-assign]
    page.gestures.human_click = MagicMock()  # type: ignore[method-assign]

    assert page.send_message("收到 谢谢", timeout_sec=0.1) is True

    page.gestures.human_type.assert_called_once_with(input_box, "收到 谢谢")
    page.gestures.human_click.assert_called_once_with(send_button)


def test_chat_page_send_message_never_sends_without_input_box():
    driver = MagicMock()
    page = ChatPage(driver)
    page.find_by_key = MagicMock(return_value=None)  # type: ignore[method-assign]
    page.gestures.human_click = MagicMock()  # type: ignore[method-assign]

    assert page.send_message("收到 谢谢", timeout_sec=0.1) is False
    page.gestures.human_click.assert_not_called()


def test_chat_page_send_message_rejects_blank_text():
    driver = MagicMock()
    page = ChatPage(driver)
    page.find_by_key = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
    page.gestures.human_click = MagicMock()  # type: ignore[method-assign]

    assert page.send_message("   ", timeout_sec=0.1) is False
    page.gestures.human_click.assert_not_called()
