"""
tests.unit.test_communication_list_page
=======================================
Unit tests for the 仅沟通 communication list page object, the chat disinterest
sequence, and the communication-list locator configuration (Issues #205, #206).
"""

from unittest.mock import MagicMock

import pytest

from boss_agent.pages import (
    CommunicationCard,
    CommunicationListPage,
    parse_company_from_descriptor,
)
from boss_agent.rejection import DISINTEREST_REASON
from droid_agent_core.locators import LocatorRegistry

NEW_LOCATOR_KEYS = (
    "communication_list.entry_tab",
    "communication_list.communication_tab",
    "communication_list.message_card",
    "communication_list.sender_name",
    "communication_list.company_position",
    "communication_list.message_text",
    "communication_list.outbound_status",
    "chat.disinterest_btn",
    "chat.disinterest_reason_option",
)


@pytest.fixture
def registry():
    return LocatorRegistry()


def test_communication_list_and_disinterest_locators_are_configured(registry):
    for key in NEW_LOCATOR_KEYS:
        assert registry.get_selectors(key), f"Missing locator configuration for '{key}'"


def test_communication_tab_locator_targets_the_jingoutong_subtab(registry):
    selectors = registry.get_selectors("communication_list.communication_tab")
    assert selectors
    assert any("仅沟通" in sel.value for sel in selectors)
    assert any("tv_title" in sel.value for sel in selectors)


def test_outbound_status_locator_targets_the_badge(registry):
    selectors = registry.get_selectors("communication_list.outbound_status")
    assert selectors
    assert any("iv_msg_status" in sel.value for sel in selectors)


def test_disinterest_reason_locator_formats_the_standardized_reason(registry):
    selectors = registry.get_selectors(
        "chat.disinterest_reason_option", format_args={"reason": DISINTEREST_REASON}
    )
    assert selectors
    assert any(DISINTEREST_REASON in sel.value for sel in selectors)


def test_inbox_message_key_is_stable_and_sender_scoped():
    first = CommunicationCard(sender_name="李女士", message_text="抱歉，暂不匹配")
    same = CommunicationCard(sender_name="李女士", message_text="抱歉，暂不匹配")
    other_sender = CommunicationCard(sender_name="王先生", message_text="抱歉，暂不匹配")
    other_text = CommunicationCard(sender_name="李女士", message_text="方便约面试吗")

    assert first.key == same.key
    assert first.key != other_sender.key
    assert first.key != other_text.key


def test_inbox_message_key_disambiguates_rows_without_a_sender_node():
    """Two recruiters sending identical rejection text must not share one key.

    Sender extraction is a locator lookup that can miss; falling back to a constant
    would collapse every such row onto a single key and silently skip the rest.
    """
    first = CommunicationCard(
        sender_name="", message_text="抱歉，暂不匹配", row_text="李女士\n抱歉，暂不匹配"
    )
    second = CommunicationCard(
        sender_name="", message_text="抱歉，暂不匹配", row_text="王先生\n抱歉，暂不匹配"
    )

    assert first.key != second.key


def test_inbox_message_key_is_stable_without_a_sender_node():
    kwargs = {"sender_name": "", "message_text": "抱歉，暂不匹配", "row_text": "李女士\n抱歉"}
    assert CommunicationCard(**kwargs).key == CommunicationCard(**kwargs).key


# ---------------------------------------------------------------------------
# Outbound Message Indicator (spec #205)
# ---------------------------------------------------------------------------


def test_outbound_indicator_is_detected_from_the_badge_text():
    delivered = CommunicationCard(
        sender_name="巫女士", message_text="我对这个岗位很感兴趣", outbound_status="[送达]"
    )
    read = CommunicationCard(
        sender_name="陈格", message_text="收到，谢谢", outbound_status="[已读]"
    )

    assert delivered.has_outbound_indicator is True
    assert read.has_outbound_indicator is True


def test_absent_badge_marks_the_card_as_inbound():
    waiting = CommunicationCard(
        sender_name="严胜",
        message_text="我们感谢您的投递，但您的专业技能与我们目前的职位需求并不完全吻合",
    )

    assert waiting.has_outbound_indicator is False
    assert waiting.outbound_status == ""


# ---------------------------------------------------------------------------
# Employer extraction from the `[Company] | [Position]` descriptor
# ---------------------------------------------------------------------------


def test_card_exposes_the_employer_parsed_from_its_descriptor():
    card = CommunicationCard(
        sender_name="严胜",
        message_text="我们感谢您的投递",
        company_position="传音控股 | 算法工程师",
    )

    assert card.company_name == "传音控股"


def test_card_without_a_descriptor_exposes_no_employer():
    card = CommunicationCard(sender_name="严胜", message_text="我们感谢您的投递")

    assert card.company_name == ""


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------


def test_is_on_inbox_detects_the_communication_tab_marker():
    driver = MagicMock()
    page = CommunicationListPage(driver)
    page.find_by_key = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]

    assert page.is_on_list(timeout_sec=0.1) is True
    page.find_by_key.assert_called_with("communication_list.communication_tab", timeout_sec=0.1)


def test_open_inbox_clicks_message_tab_then_communication_tab():
    driver = MagicMock()
    page = CommunicationListPage(driver)
    marker = MagicMock()
    page.find_by_key = MagicMock(return_value=marker)  # type: ignore[method-assign]
    page.gestures.human_click = MagicMock()  # type: ignore[method-assign]

    assert page.open_list(timeout_sec=0.1) is True

    clicked_keys = [call.args[0] for call in page.find_by_key.call_args_list]
    assert clicked_keys == [
        "communication_list.entry_tab",
        "communication_list.communication_tab",
    ]
    assert page.gestures.human_click.call_count == 2


def test_open_inbox_reports_failure_without_communication_tab():
    driver = MagicMock()
    page = CommunicationListPage(driver)
    page.find_by_key = MagicMock(  # type: ignore[method-assign]
        side_effect=lambda key, **_: MagicMock() if key == "communication_list.entry_tab" else None
    )

    assert page.open_list(timeout_sec=0.1) is False


# ---------------------------------------------------------------------------
# Card extraction
# ---------------------------------------------------------------------------


def _message_card(sender: str, text: str, *, status: str = "", descriptor: str = "") -> MagicMock:
    """Build a fake communication card whose sub-element lookups return real nodes."""
    sender_node = MagicMock(text=sender)
    text_node = MagicMock(text=text)
    status_node = MagicMock(text=status) if status else None
    descriptor_node = MagicMock(text=descriptor) if descriptor else None

    def find_elements(by, value):
        if "iv_msg_status" in value:
            return [status_node] if status_node else []
        if "tv_msg" in value:
            return [text_node]
        if "tv_name" in value:
            return [sender_node]
        if "company" in value or "job_name" in value or "position" in value:
            return [descriptor_node] if descriptor_node else []
        return []

    card = MagicMock(text=f"{sender}\n{text}")
    card.find_elements.side_effect = find_elements
    return card


def _page_with_cards(cards: list[MagicMock]) -> CommunicationListPage:
    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}
    page = CommunicationListPage(driver)
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


def test_extract_visible_messages_reads_the_outbound_badge():
    outbound = _message_card("巫女士", "我对这个岗位很感兴趣", status="[送达]")
    inbound = _message_card("严胜", "我们感谢您的投递")
    page = _page_with_cards([outbound, inbound])

    messages = page.extract_visible_messages()

    assert [m.has_outbound_indicator for m in messages] == [True, False]
    assert messages[0].outbound_status == "[送达]"


def test_extract_visible_messages_reads_the_company_position_descriptor():
    card = _message_card("严胜", "我们感谢您的投递", descriptor="传音控股 | 算法工程师")
    page = _page_with_cards([card])

    messages = page.extract_visible_messages()

    assert messages[0].company_position == "传音控股 | 算法工程师"
    assert messages[0].company_name == "传音控股"


def test_extract_visible_messages_falls_back_to_a_child_descriptor_node():
    """The descriptor has no measured resource-id, so a text scan backs it up."""
    card = _message_card("严胜", "我们感谢您的投递")
    descriptor_node = MagicMock(text="磐基技术 | 技术总监")
    card.find_elements.side_effect = lambda by, value: (
        [descriptor_node] if "TextView" in value else []
    )
    page = _page_with_cards([card])

    messages = page.extract_visible_messages()

    assert messages[0].company_name == "磐基技术"


def test_child_descriptor_scan_never_returns_the_message_as_an_employer():
    """A text-only card must not turn its own message into a blacklisted employer."""
    card = _message_card("严胜", "抱歉，您的简历虽优秀 | 但与我们当前职位不匹配")
    card.find_elements.side_effect = lambda by, value: (
        [MagicMock(text="抱歉，您的简历虽优秀 | 但与我们当前职位不匹配")]
        if "TextView" in value
        else []
    )
    page = _page_with_cards([card])

    messages = page.extract_visible_messages()

    assert messages[0].company_position == ""
    assert messages[0].company_name == ""


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

    page.scroll_list()

    assert page.gestures.human_swipe.call_count == 1
    start, end = page.gestures.human_swipe.call_args.args[:2]
    assert start.y > end.y


def test_open_message_uses_humanized_click():
    card = _message_card("李女士", "暂不匹配")
    page = _page_with_cards([card])
    page.gestures.human_click = MagicMock()  # type: ignore[method-assign]
    message = CommunicationCard(sender_name="李女士", message_text="暂不匹配", element=card)

    assert page.open_message(message) is True
    page.gestures.human_click.assert_called_once_with(card)


def test_open_message_without_element_is_a_noop():
    page = _page_with_cards([])

    assert (
        page.open_message(CommunicationCard(sender_name="李女士", message_text="暂不匹配")) is False
    )


def test_parse_company_from_descriptor_is_exposed_for_the_blacklist_path():
    assert parse_company_from_descriptor("传音控股 | 算法工程师") == "传音控股"
