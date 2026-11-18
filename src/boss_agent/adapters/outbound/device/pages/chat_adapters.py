"""
boss_agent.adapters.outbound.device.pages.chat_adapters
=======================================================
The production device ports: the 仅沟通 list and chat page objects, behind triage's
vocabulary.
"""

from .communication import ChatPage, CommunicationCard, CommunicationListPage

#: Timeout budget for a single list/dialog interaction.
PAGE_TIMEOUT_SEC = 5.0

#: Timeout budget for the cheap "is the list already showing" probe.
LIST_PROBE_TIMEOUT_SEC = 1.0


class CommunicationListAdapter:
    """The production list reader: the 仅沟通 page object, behind triage's vocabulary."""

    def __init__(self, page: CommunicationListPage, timeout_sec: float = PAGE_TIMEOUT_SEC) -> None:
        self._page = page
        self._timeout_sec = timeout_sec

    def is_on_list(self) -> bool:
        return self._page.is_on_list(timeout_sec=LIST_PROBE_TIMEOUT_SEC)

    def ensure_open_list(self) -> bool:
        return self._page.open_list(timeout_sec=self._timeout_sec)

    def scroll_to_top(self) -> bool:
        return self._page.scroll_to_top()

    def visible_cards(self, max_items: int) -> list[CommunicationCard]:
        return self._page.extract_visible_messages(max_items=max_items)

    def open_card(self, card: CommunicationCard) -> bool:
        return self._page.open_message(card)

    def mark_disinterest(self) -> bool:
        return self._page.mark_disinterest(timeout_sec=self._timeout_sec)

    def confirm_back_on_list(self) -> bool:
        return self._page.wait_for_list_return(timeout_sec=self._timeout_sec)

    def has_message_tab_unread_dot(self) -> bool:
        return self._page.has_message_tab_unread_dot(timeout_sec=LIST_PROBE_TIMEOUT_SEC)

    def get_unread_badge_count(self) -> int | None:
        return self._page.get_unread_badge_count(timeout_sec=LIST_PROBE_TIMEOUT_SEC)

    def scroll_list_down(self) -> bool:
        return self._page.scroll_message_list()


class ChatActorAdapter:
    """The production chat actor: the chat page object, behind triage's vocabulary."""

    def __init__(self, page: ChatPage, timeout_sec: float = PAGE_TIMEOUT_SEC) -> None:
        self._page = page
        self._timeout_sec = timeout_sec

    def send_reply(self, text: str) -> bool:
        return self._page.send_message(text, timeout_sec=self._timeout_sec)

    def back_to_list(self) -> bool:
        return self._page.navigate_back(timeout_sec=self._timeout_sec)


__all__ = [
    "ChatActorAdapter",
    "CommunicationListAdapter",
    "LIST_PROBE_TIMEOUT_SEC",
    "PAGE_TIMEOUT_SEC",
]
