"""
tests.unit.test_communication_list_page
=======================================
Unit tests for the 仅沟通 communication list page object, the chat disinterest
sequence, and the communication-list locator configuration (Issues #205, #206).
"""

import time
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


def test_message_card_locator_targets_recyclerview_items(registry):
    selectors = registry.get_selectors("communication_list.message_card")
    assert selectors
    assert any("recyclerView" in sel.value for sel in selectors)


def test_company_position_locator_targets_tv_position(registry):
    selectors = registry.get_selectors("communication_list.company_position")
    assert selectors
    assert any("tv_position" in sel.value for sel in selectors)


def test_disinterest_reason_locator_formats_the_standardized_reason(registry):
    selectors = registry.get_selectors(
        "chat.disinterest_reason_option", format_args={"reason": DISINTEREST_REASON}
    )
    assert selectors
    assert any(DISINTEREST_REASON in sel.value for sel in selectors)


def test_message_key_is_stable_and_sender_scoped():
    first = CommunicationCard(sender_name="李女士", message_text="抱歉，暂不匹配")
    same = CommunicationCard(sender_name="李女士", message_text="抱歉，暂不匹配")
    other_sender = CommunicationCard(sender_name="王先生", message_text="抱歉，暂不匹配")
    other_text = CommunicationCard(sender_name="李女士", message_text="方便约面试吗")

    assert first.key == same.key
    assert first.key != other_sender.key
    assert first.key != other_text.key


def test_message_key_disambiguates_rows_without_a_sender_node():
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


def test_message_key_distinguishes_two_employers_sharing_a_generic_sender_name():
    """Generic names ('李女士') plus one canned rejection template repeat across employers.

    Sender and text alone would collide, and the later card is then skipped as
    already-visited -- so its company is never blacklisted.
    """
    first = CommunicationCard(
        sender_name="李女士",
        message_text="您好，感谢关注，但您的经历与该岗位不太匹配",
        company_position="甲科技 | 后端开发",
    )
    second = CommunicationCard(
        sender_name="李女士",
        message_text="您好，感谢关注，但您的经历与该岗位不太匹配",
        company_position="乙科技 | 后端开发",
    )

    assert first.key != second.key


def test_message_key_is_stable_across_rereads_of_the_same_card():
    kwargs = {
        "sender_name": "李女士",
        "message_text": "暂不匹配",
        "company_position": "征图新视 | 算法工程师",
    }

    assert CommunicationCard(**kwargs).key == CommunicationCard(**kwargs).key


def test_message_key_is_stable_without_a_sender_node():
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
    draft = CommunicationCard(
        sender_name="宋女士", message_text="收到 谢谢", outbound_status="[草稿]"
    )

    assert delivered.has_outbound_indicator is True
    assert read.has_outbound_indicator is True
    assert draft.has_outbound_indicator is True


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


def test_is_on_list_detects_the_communication_tab_marker():
    driver = MagicMock()
    page = CommunicationListPage(driver)
    page.find_by_key = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]

    assert page.is_on_list(timeout_sec=0.1) is True
    page.find_by_key.assert_called_with("communication_list.communication_tab", timeout_sec=0.1)


BOSS_PACKAGE = "com.hpbr.bosszhipin"
LAUNCHER_PACKAGE = "com.android.launcher3"

#: A stack deep enough that no bounded recovery loop can ever unwind it.
UNREACHABLE_DEPTH = 99


class _Element:
    """Stand-in for an Appium node: identity is what the click assertions compare."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.name}>"


class _NavDriver:
    """Fake Appium driver modelling a bounded back stack over the Boss app.

    ``depth`` counts the screens stacked above the 消息 column: unwinding it to zero
    lands on the 仅沟通 list (the platform keeps the filter selected), while a Back
    press past the app root leaves Boss for the launcher — the failure the recovery
    loop exists to guard against.
    """

    def __init__(self, *, depth: int = 0, back_button: bool = True, on_list: bool = False):
        self.depth = depth
        self.back_button = back_button
        self.on_list = on_list
        self.package = BOSS_PACKAGE
        self.keycodes: list[int] = []
        self.activated: list[str] = []

    @property
    def current_package(self) -> str:
        return self.package

    def press_keycode(self, code: int) -> None:
        self.keycodes.append(code)
        self._unwind()

    def activate_app(self, package: str) -> None:
        self.activated.append(package)
        self.package = package

    def _unwind(self) -> None:
        self.depth -= 1
        if self.depth < 0:
            self.package = LAUNCHER_PACKAGE
        elif self.depth == 0:
            self.on_list = True


def _recovery_page(driver: _NavDriver) -> CommunicationListPage:
    """Wire a page object's probes onto the fake driver's screen state."""
    page = CommunicationListPage(driver)
    message_tab, communication_tab, back_btn = (
        _Element("消息"),
        _Element("仅沟通"),
        _Element("iv_back"),
    )

    def find(key, **_):
        if key == "communication_list.communication_tab":
            return communication_tab if driver.on_list else None
        if key == "communication_list.entry_tab":
            if driver.depth == 0 and driver.package == BOSS_PACKAGE:
                return message_tab
            return None
        if key == "communication_list.back_btn":
            if driver.back_button and driver.depth > 0:
                return back_btn
            return None
        return None

    def click(elem):
        if elem is back_btn:
            driver._unwind()
        elif elem is message_tab:
            driver.on_list = True

    page.find_by_key = MagicMock(side_effect=find)  # type: ignore[method-assign]
    page.gestures.human_click = MagicMock(side_effect=click)  # type: ignore[method-assign]
    page.gestures.random_sleep = MagicMock()  # type: ignore[method-assign]
    return page


def test_open_list_returns_immediately_when_already_on_the_list():
    """#228: a worker already on the list must not touch the screen at all."""
    driver = _NavDriver(on_list=True)
    page = _recovery_page(driver)

    assert page.open_list(timeout_sec=0.1) is True
    assert page.gestures.human_click.call_count == 0
    assert driver.keycodes == []


def test_open_list_clicks_message_tab_then_communication_tab():
    driver = _NavDriver(on_list=False)
    page = _recovery_page(driver)

    assert page.open_list(timeout_sec=0.1) is True

    clicked = [call.args[0].name for call in page.gestures.human_click.call_args_list]
    assert clicked == ["消息", "仅沟通"]
    assert driver.keycodes == []


def test_open_list_reports_failure_without_communication_tab():
    driver = MagicMock()
    page = CommunicationListPage(driver)
    page.find_by_key = MagicMock(  # type: ignore[method-assign]
        side_effect=lambda key, **_: MagicMock() if key == "communication_list.entry_tab" else None
    )

    assert page.open_list(timeout_sec=0.1, max_steps=2) is False


def test_recovery_prefers_the_on_screen_back_button_over_the_hardware_key():
    """#228: Back-button clicks keep the app inside Boss; the key can walk out of it."""
    driver = _NavDriver(depth=2)
    page = _recovery_page(driver)

    assert page.open_list(timeout_sec=0.1, max_steps=4) is True
    assert driver.keycodes == [], "an on-screen back button was available the whole way"
    assert [c.args[0].name for c in page.gestures.human_click.call_args_list] == [
        "iv_back",
        "iv_back",
    ]


def test_recovery_falls_back_to_the_hardware_back_key():
    driver = _NavDriver(depth=1, back_button=False)
    page = _recovery_page(driver)

    assert page.open_list(timeout_sec=0.1, max_steps=4) is True
    assert driver.keycodes == [4]


def test_recovery_reactivates_boss_when_back_escapes_to_the_launcher():
    driver = _NavDriver(depth=0, back_button=False)
    driver.package = LAUNCHER_PACKAGE
    page = _recovery_page(driver)

    assert page.open_list(timeout_sec=0.1, max_steps=2) is False
    assert driver.activated == [BOSS_PACKAGE, BOSS_PACKAGE]


def test_recovery_loop_is_bounded_by_the_step_budget():
    driver = _NavDriver(depth=UNREACHABLE_DEPTH, back_button=False)
    page = _recovery_page(driver)

    assert page.open_list(timeout_sec=0.1, max_steps=3) is False
    assert driver.keycodes == [4, 4, 4]


def test_recovery_steps_never_wait_out_the_element_timeout():
    """Each step is a fast-fail probe: polling the full budget per step would stall a run.

    Wired against the real locator lookup (one accessibility query per candidate) rather
    than the scripted screen fake, because the cost being asserted *is* the lookup's.
    """
    driver = MagicMock()
    driver.find_elements.return_value = []
    page = CommunicationListPage(driver)
    page.gestures.human_click = MagicMock()  # type: ignore[method-assign]
    page.gestures.random_sleep = MagicMock()  # type: ignore[method-assign]
    page.press_back = MagicMock()  # type: ignore[method-assign]

    started = time.monotonic()
    assert page.open_list(timeout_sec=5.0, max_steps=3) is False

    assert time.monotonic() - started < 2.0


def test_back_button_locator_targets_the_boss_page_back_affordances(registry):
    """#228: the recovery probe must resolve the measured in-app back buttons first."""
    selectors = registry.get_selectors("communication_list.back_btn")
    assert selectors
    values = [sel.value for sel in selectors]
    assert values[0] == "com.hpbr.bosszhipin:id/iv_back"
    assert "com.hpbr.bosszhipin:id/iv_back_ai" in values


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


def test_extract_visible_messages_reads_a_fullwidth_separator_descriptor():
    """The divider is a rendered glyph: some builds emit '｜' (U+FF5C), not '|'.

    Both the field lookup and the child-node scan gate on the separator, so an
    unnormalised fullwidth bar yields no employer for every card on such a device.
    """
    card = _message_card("严胜", "我们感谢您的投递", descriptor="传音控股｜算法工程师")
    page = _page_with_cards([card])

    messages = page.extract_visible_messages()

    assert messages[0].company_name == "传音控股"


def test_child_descriptor_scan_accepts_a_fullwidth_separator():
    card = _message_card("严胜", "我们感谢您的投递")
    descriptor_node = MagicMock(text="磐基技术｜技术总监")
    card.find_elements.side_effect = lambda by, value: (
        [descriptor_node] if "TextView" in value else []
    )
    page = _page_with_cards([card])

    messages = page.extract_visible_messages()

    assert messages[0].company_name == "磐基技术"


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


def test_scroll_list_swipes_upward():
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


def test_find_message_cards_resolves_parent_container_on_fallback():
    driver = MagicMock()
    parent_card = MagicMock()
    text_node = MagicMock()
    text_node.find_element.return_value = parent_card

    page = CommunicationListPage(driver)
    page._find_elements_by_key = MagicMock(
        side_effect=lambda key, **_: [] if key == "communication_list.message_card" else [text_node]
    )

    cards = page._find_message_cards()
    assert cards == [parent_card]
    text_node.find_element.assert_called_with(by="xpath", value="..")

