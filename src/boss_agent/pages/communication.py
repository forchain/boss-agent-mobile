"""
pages.communication
====================
The 仅沟通 communication list, the chat page, and the card model they produce
(spec #395, ticket #398).

Chat triage needs the card dataclass, its deduplication key, the employer parser, and the outbound
status markers -- and none of the job-feed or filter-dialog adapters behind them. Badge text is
matched explicitly against `OUTBOUND_STATUS_MARKERS` rather than keyed on a node's mere presence,
so an unrecognised badge still fails toward one extra LLM call instead of a silently unpopulated
blacklist.
"""

import contextlib
import hashlib
import re
from dataclasses import dataclass
from typing import Any

from droid_agent_core.gestures import Point

from ..rejection import DISINTEREST_REASON
from .base import BACK_INTERVAL_SEC, BaseBossPage, ui_logger

#: Separator in a communication card's `[Company] | [Position]` descriptor.
CARD_DESCRIPTOR_SEPARATOR: str = "|"


#: Fullwidth vertical bar (U+FF5C). The divider is a rendered glyph rather than a
#: protocol value, so both forms are accepted: a build or font that emits the
#: fullwidth bar would otherwise yield no employer on any card, and the blacklist
#: would silently never be populated.
FULLWIDTH_CARD_DESCRIPTOR_SEPARATOR: str = "｜"


#: Locator key of the on-screen back affordance tried before the hardware Back key.
BACK_BUTTON_KEY: str = "communication_list.back_btn"


#: XPath to a card's text nodes, the fallback for fields the platform leaves unlabelled.
CARD_CHILD_TEXT_XPATH: str = ".//android.widget.TextView"


#: Bounded recovery steps the 仅沟通 list navigation may spend unwinding the app.
LIST_RECOVERY_MAX_STEPS: int = 6


def _normalize_card_descriptors(text: str) -> str:
    """Collapse fullwidth vertical bars onto the ASCII card-descriptor separator."""
    return (text or "").replace(FULLWIDTH_CARD_DESCRIPTOR_SEPARATOR, CARD_DESCRIPTOR_SEPARATOR)


#: Badge texts the platform renders in `iv_msg_status` for a thread whose last
#: message is the candidate's own or an unsent draft (spec #205). Matched explicitly
#: rather than keyed on the node's mere presence: a future badge with different wording
#: would otherwise silently stop every rejection from being detected.
OUTBOUND_STATUS_MARKERS: tuple[str, ...] = ("送达", "已读", "草稿")


def _whitespace_digest(text: str) -> str:
    normalized = re.sub(r"\s+", "", text or "")
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def compute_card_key(
    sender_name: str,
    message_text: str,
    row_text: str = "",
    descriptor: str = "",
) -> str:
    """Signature identifying one communication card for visited-set deduplication.

    Sender, the card's employer descriptor, and a whitespace-insensitive hash of the
    message text: stable across re-reads of the same card, and distinct for two
    recruiters sending identical text.

    The descriptor earns its place because platform rejection templates are canned
    and generic sender names ('李女士', 'HR', '招聘负责人') repeat across employers:
    sender plus text alone collapses two different companies onto one key, and the
    later card is skipped as already-visited -- silently costing it its blacklist
    entry, which is the one durable action this path takes.

    When the sender node cannot be read, the row's own rendered text stands in for
    it. Falling back to a constant would collapse identical rejection templates
    from different recruiters onto one key, silently skipping the later ones.
    """
    sender = re.sub(r"\s+", "", sender_name or "").strip()
    if not sender:
        source = re.sub(r"\s+", "", row_text or "") or re.sub(r"\s+", "", message_text or "")
        sender = f"row-{hashlib.sha1(source.encode('utf-8')).hexdigest()[:8]}"
    employer = _normalize_card_descriptors(descriptor)
    return f"{sender}:{_whitespace_digest(employer)}:{_whitespace_digest(message_text)}"


def parse_company_from_descriptor(descriptor: str, sender_name: str = "") -> str:
    """Extract the employer from a card's `[Company] | [Position]` descriptor.

    Examples:
        "传音控股 | 算法工程师"        -> "传音控股"
        "严胜 传音控股 | 算法工程师"   -> "传音控股"   (sender_name="严胜")
        "小米集团 | 算法工程师"        -> "小米集团"   (sender_name="小米")
        "我们感谢您的投递"            -> ""            (no separator)

    Anything after the first separator is the position and is discarded: only the
    employer is ever blacklisted.

    A leading recruiter name is stripped because the row-text fallback can glue it
    onto the descriptor -- but only when a delimiter actually separates the two.
    This value feeds a substring-matched *global* blacklist, so a sender whose
    nickname merely prefixes the employer must leave it intact: stripping "小米"
    off "小米集团" leaves the generic token "集团", which would then reject every
    集团 employer in the country. An undelimited prefix is left alone deliberately:
    the worst case is an employer name that matches nothing, against a worst case of
    blacklisting an entire class of employers.
    """
    text = _normalize_card_descriptors(descriptor).strip()
    if CARD_DESCRIPTOR_SEPARATOR not in text:
        return ""

    company = text.split(CARD_DESCRIPTOR_SEPARATOR, 1)[0].strip()
    sender = (sender_name or "").strip()
    if sender and company != sender:
        glued = re.match(rf"^{re.escape(sender)}[\s·•・:：\-—－]+(?P<employer>.+)$", company)
        if glued:
            company = glued.group("employer").strip()
    return company


@dataclass
class CommunicationCard:
    """One conversation card read from the 仅沟通 communication list.

    Everything the triage needs is on the card itself: the outbound indicator
    says whether the thread is waiting on the recruiter, and the descriptor
    carries the employer.
    """

    sender_name: str
    message_text: str
    element: Any = None
    row_text: str = ""
    key: str = ""
    #: Raw Outbound Message Indicator text (`iv_msg_status`), e.g. "[送达]".
    outbound_status: str = ""
    #: The card's `[Company] | [Position]` descriptor, e.g. "传音控股 | 算法工程师".
    company_position: str = ""

    def __post_init__(self) -> None:
        if not self.key:
            self.key = compute_card_key(
                self.sender_name,
                self.message_text,
                self.row_text,
                descriptor=self.company_position,
            )

    @property
    def has_outbound_indicator(self) -> bool:
        """Whether the candidate sent the last message and the recruiter has not replied.

        True only for a badge carrying a known marker (``[送达]`` / ``[已读]``). An
        unrecognised badge is treated as an inbound message instead of being
        skipped: a missed skip costs one LLM call, while a wrong skip would stop
        every rejection from ever being blacklisted, silently and invisibly.
        """
        status = (self.outbound_status or "").strip()
        return any(marker in status for marker in OUTBOUND_STATUS_MARKERS)

    @property
    def company_name(self) -> str:
        """Employer parsed from the card descriptor, or "" when unavailable."""
        return parse_company_from_descriptor(self.company_position, self.sender_name)


class ChatPage(BaseBossPage):
    """Interacts with the Boss 直聘 chat/greeting communication page."""

    def is_chat_page(self, timeout_sec: float = 3.0) -> bool:
        """Check if currently inside chat dialog/page."""
        return bool(self.find_by_key("chat.message_input", timeout_sec=timeout_sec))

    def open_chat(self, timeout_sec: float = 5.0) -> bool:
        """Click '立即沟通' / chat entry button to open chat dialogue."""
        elem = self.find_by_key("chat.chat_entry_btn", timeout_sec=timeout_sec)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False

    def type_greeting_message(
        self, message: str, timeout_sec: float = 5.0, clear_first: bool = False
    ) -> bool:
        """Type greeting message into the chat message input box.

        IMPORTANT SAFETY GUARANTEE: Does NOT click the send button.
        Allows user to review, edit, or manually send during testing.
        """
        elem = self.find_by_key("chat.message_input", timeout_sec=timeout_sec)
        if elem:
            if clear_first:
                self.gestures.human_type(elem, message, clear_first=True)
            else:
                self.gestures.human_type(elem, message)
            return True
        return False

    def clear_message_input(self, timeout_sec: float = 3.0) -> bool:
        """Clear any text from the chat message input box to avoid leaving drafts."""
        elem = self.find_by_key("chat.message_input", timeout_sec=timeout_sec)
        if elem and hasattr(elem, "clear"):
            with contextlib.suppress(Exception):
                elem.clear()
                return True
        return False

    def click_send(self, timeout_sec: float = 3.0) -> bool:
        """Click the send button on the chat page."""
        elem = self.find_by_key("chat.send_btn", timeout_sec=timeout_sec)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False

    def send_message(self, message: str, timeout_sec: float = 5.0) -> bool:
        """Type and send a chat message in one step.

        Only ever reached for messages already judged safe to send (e.g. a
        rejection acknowledgment). Blank text is refused so an empty input box
        can never be submitted.
        """
        if not (message or "").strip():
            return False
        if not self.type_greeting_message(message, timeout_sec=timeout_sec, clear_first=True):
            return False
        return self.click_send(timeout_sec=timeout_sec)

    def navigate_back(self, timeout_sec: float = 3.0) -> bool:
        """Click back button from chat dialog."""
        elem = self.find_by_key("chat.back_btn", timeout_sec=timeout_sec)
        if elem:
            self.gestures.human_click(elem)
            return True
        # Fallback to driver back if element not found
        try:
            self.driver.back()
            return True
        except Exception:
            return False


class CommunicationListPage(BaseBossPage):
    """Page Object for the 仅沟通 communication list under the 消息 tab.

    Cards carry everything the triage needs — the Outbound Message Indicator, the
    `[Company] | [Position]` descriptor and the last message text — so neither
    classification nor employer extraction ever requires opening a chat (spec #205).
    """

    def is_on_list(self, timeout_sec: float = 2.0) -> bool:
        """Check whether the 仅沟通 communication list is currently displayed."""
        return (
            self.find_by_key("communication_list.communication_tab", timeout_sec=timeout_sec)
            is not None
        )

    def has_message_tab_unread_dot(self, timeout_sec: float = 1.0) -> bool:
        """Check whether the bottom navigation 消息 tab currently shows an unread red dot.

        One reading of the Tier 1 preflight probe. With the bottom navigation bar
        present (entry_tab found), an absent `communication_list.message_tab_unread_dot`
        is what a zero-unread account looks like — but so is a bar that has only just
        rendered, so the caller confirms the absence over a settle window before it
        trusts one reading. If the bottom navigation bar is not visible on the current
        screen (e.g. within an inner chat or subpage), returns True so the caller
        proceeds with standard list recovery: nothing can be concluded about an account
        whose navigation is not on screen.
        """
        entry_tab = self.find_by_key("communication_list.entry_tab", timeout_sec=timeout_sec)
        if not entry_tab:
            return True
        return self.find_now("communication_list.message_tab_unread_dot") is not None

    def get_unread_badge_count(self, timeout_sec: float = 1.0) -> int | None:
        """Read the unread count badge on the 仅沟通 category sub-tab.

        Returns the parsed integer count (e.g. 1, 5, 99) when the badge is present, or
        None when it is absent — which means 0 unread in 仅沟通 *if* the badge has been
        drawn. One reading cannot tell those apart, so the caller re-reads a cleared or
        absent badge across a settle window before it treats the category as zero-unread.
        """
        elem = self.find_by_key("communication_list.unread_badge", timeout_sec=timeout_sec)
        if elem is None:
            return None
        raw = getattr(elem, "text", "")
        if not isinstance(raw, str):
            return None
        raw_text = raw.strip()
        if not raw_text:
            return None
        import re

        digits = re.sub(r"[^\d]", "", raw_text)
        if digits:
            return int(digits)
        return 1

    def scroll_message_list(self) -> bool:
        """Perform a humanized scroll downwards on the conversation RecyclerView.

        Prefers the bounds of the RecyclerView container when found, falling back
        to window dimensions. Returns True if the gesture was performed, False otherwise.
        """
        if not self.driver:
            return False
        recycler = self.find_now("communication_list.recycler_view")
        rect = getattr(recycler, "rect", None) if recycler else None
        if rect and isinstance(rect, dict) and rect.get("width") and rect.get("height"):
            w = rect["width"]
            h = rect["height"]
            x = rect.get("x", 0)
            y = rect.get("y", 0)
            start = Point(x + w * 0.5, y + h * 0.75)
            end = Point(x + w * 0.5, y + h * 0.25)
        else:
            size = self._get_window_size()
            w, h = size["width"], size["height"]
            start = Point(w * 0.5, h * 0.75)
            end = Point(w * 0.5, h * 0.25)
        self.gestures.human_swipe(start, end, duration_ms=500)
        self.gestures.random_sleep(0.3, 0.6)
        return True

    def wait_for_list_return(self, timeout_sec: float = 5.0) -> bool:
        """Wait for the platform to drop the conversation and land back on the list."""
        return self.is_on_list(timeout_sec=timeout_sec)

    def open_list(self, timeout_sec: float = 5.0, max_steps: int = LIST_RECOVERY_MAX_STEPS) -> bool:
        """Navigate into the 仅沟通 list from whatever screen the app is currently on.

        The message column and the 仅沟通 sub-tab are directly clicked. Because Android
        accessibility and page source do not differentiate which sub-tab (全部, 新招呼,
        仅沟通, 有交换) is active, presence of the tab marker cannot be used to bypass
        clicks. Clicking 消息 -> 仅沟通 ensures the 仅沟通 list is explicitly active.

        From anywhere else the loop spends at most ``max_steps`` screens trying to
        reach the message column, then clicks 消息 -> 仅沟通 and confirms the landing.

        A CHECK_CHAT dispatch can arrive while the app sits on a job detail, an open
        chat, a filter sheet or the launcher, so "the 消息 tab is right there" cannot
        be assumed: unwinding first, and clicking the column the moment it appears,
        is what makes the navigation self-healing instead of an instant task failure.

        The per-step probes fast-fail; only the landing confirmation inside
        `_open_message_column` spends the caller's ``timeout_sec``, because polling
        out the full element budget on each of ``max_steps`` screens turns one
        unreachable list into a multi-minute stall.
        """
        for step in range(max_steps + 1):
            if self._open_message_column(timeout_sec=timeout_sec):
                return True
            if step == max_steps:
                break
            ui_logger.debug("[UI] 仅沟通 recovery step %d/%d", step + 1, max_steps)
            self._recover_one_step()
        return False

    def _open_message_column(self, timeout_sec: float) -> bool:
        """Click the bottom 消息 tab then the 仅沟通 sub-tab; True once landed on the list."""
        tab = self.find_now("communication_list.entry_tab")
        if not tab:
            return False
        self.gestures.human_click(tab)

        sub_tab = self.find_by_key("communication_list.communication_tab", timeout_sec=timeout_sec)
        if not sub_tab:
            return False
        self.gestures.human_click(sub_tab)
        self.gestures.random_sleep(0.2, 0.4)
        return self.is_on_list(timeout_sec=timeout_sec)

    def _recover_one_step(self) -> None:
        """Unwind exactly one screen: on-screen back button first, hardware key second.

        The on-screen affordance is preferred because it keeps the app inside Boss,
        whereas a hardware Back from a chat room or the message column can leave the
        app entirely. The probe stays a single short locator list on purpose (ADR
        0011 measured each missed candidate at ~1s), the foreground guard re-activates
        Boss if a Back press did escape, and the settle pause after either action is
        what stops the loop degenerating into a runaway burst of clicks.
        """
        self._ensure_foreground()
        back = self.find_now(BACK_BUTTON_KEY)
        if back:
            self.gestures.human_click(back)
        else:
            self.press_back()
        self.gestures.random_sleep(*BACK_INTERVAL_SEC)

    def _find_message_cards(self) -> list[Any]:
        """Locate communication rows, falling back to the message nodes' parents."""
        cards = self._find_elements_by_key("communication_list.message_card")
        if cards:
            return cards
        text_nodes = self._find_elements_by_key("communication_list.message_text")
        if not text_nodes:
            return []
        resolved: list[Any] = []
        for node in text_nodes:
            try:
                parent = node.find_element(by="xpath", value="..")
                resolved.append(parent if parent else node)
            except Exception:
                resolved.append(node)
        return resolved

    def extract_visible_messages(self, max_items: int = 10) -> list[CommunicationCard]:
        """Extract every visible card's sender, outbound badge, descriptor and text."""
        if not self.driver:
            return []

        cards: list[CommunicationCard] = []
        for card in self._find_message_cards()[:max_items]:
            row_text = (getattr(card, "text", "") or "").strip()
            sender = self._extract_card_field_text(card, "communication_list.sender_name")
            text = self._extract_card_field_text(card, "communication_list.message_text")
            if not text:
                # A card may itself be the message node (fallback locator path).
                text = row_text
            if not text:
                continue
            cards.append(
                CommunicationCard(
                    sender_name=sender,
                    message_text=text,
                    element=card,
                    row_text=row_text,
                    outbound_status=self._extract_card_field_text(
                        card, "communication_list.outbound_status"
                    ),
                    company_position=self._extract_company_position(card, sender, text),
                )
            )
        return cards

    def _scan_card_text_nodes(self, card: Any) -> list[str]:
        """The text of a card's child TextViews, in document order.

        The fallback behind the fields the platform leaves unlabelled: the descriptor
        is rendered into a text node with no resource-id measured on hardware, and a
        node walk is what reads it. A card whose layer cannot be read at all yields
        nothing, which the caller reads as "this field is not on the card" rather
        than as an error.
        """
        try:
            children = card.find_elements(by="xpath", value=CARD_CHILD_TEXT_XPATH)
        except Exception:
            return []
        return [(getattr(child, "text", "") or "").strip() for child in children]

    def _extract_company_position(self, card: Any, sender: str, message_text: str) -> str:
        """Read the `[Company] | [Position]` descriptor off a card.

        Falls back to the card's own child nodes when the configured resource-ids
        miss, because the descriptor node has no measured id on a live device.

        The sender, the message and the rendered row are all excluded: a rejection
        message may itself contain a `|`, and mistaking the message for an employer
        would blacklist a company that was never named on the card.

        Fullwidth bars are folded onto the ASCII separator before every comparison --
        the separator is a rendered glyph, and normalising only the configured field
        would leave the node scan blind on a device that draws `｜`.
        """
        descriptor = _normalize_card_descriptors(
            self._extract_card_field_text(card, "communication_list.company_position")
        )
        if CARD_DESCRIPTOR_SEPARATOR in descriptor:
            return descriptor

        message = _normalize_card_descriptors((message_text or "").strip())
        sender = (sender or "").strip()
        for candidate in self._scan_card_text_nodes(card):
            candidate = _normalize_card_descriptors(candidate)
            if not candidate or candidate == sender:
                continue
            if message and (candidate in message or message in candidate):
                continue
            if CARD_DESCRIPTOR_SEPARATOR in candidate:
                return candidate
        return ""

    def open_message(self, message: CommunicationCard) -> bool:
        """Click a communication card to enter its chat dialog."""
        if message.element is None:
            return False
        self.gestures.human_click(message.element)
        return True

    def mark_disinterest(self, timeout_sec: float = 3.0) -> bool:
        """Submit disinterest feedback with the standardized 重复推荐 reason.

        Fails fast rather than guessing: if either the 不感兴趣 button or the
        reason option does not appear, the sequence aborts without a second click
        so the caller can report the unexpected UI. The reason is deliberately not
        a parameter: the platform feedback category is standardized and never
        branched on.
        """
        button = self.find_by_key("chat.disinterest_btn", timeout_sec=timeout_sec)
        if not button:
            return False
        self.gestures.human_click(button)

        option = self.find_by_key(
            "chat.disinterest_reason_option",
            timeout_sec=timeout_sec,
            format_args={"reason": DISINTEREST_REASON},
        )
        if not option:
            return False
        self.gestures.human_click(option)
        return True
