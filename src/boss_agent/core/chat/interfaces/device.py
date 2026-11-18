"""
boss_agent.core.chat.interfaces.device
=======================================
The 仅沟通 device world as triage needs it (ADR 0016).

The two ports are cut at the list and the chat rather than at the driver, so a run
holds cards as data and can reach nothing else. 仅沟通 List Recovery, card extraction
and descriptor parsing stay inside the page objects behind these protocols; production
adapters and their composition live in
``boss_agent.adapters.outbound.device.pages.chat_adapters`` and
``boss_agent.adapters.outbound.device.pages.chat_bundle``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class CommunicationCard(Protocol):
    """The fields one 仅沟通 card exposes to triage, and nothing else."""

    sender_name: str
    message_text: str
    key: str
    #: Raw Outbound Message Indicator text (`iv_msg_status`), e.g. "[送达]".
    outbound_status: str
    #: The card's `[Company] | [Position]` descriptor, e.g. "传音控股 | 算法工程师".
    company_position: str

    @property
    def has_outbound_indicator(self) -> bool:
        """Whether the candidate sent the last message and the recruiter has not replied."""

    @property
    def company_name(self) -> str:
        """Employer parsed from the card descriptor, or "" when unavailable."""


@runtime_checkable
class ChatListReader(Protocol):
    """The 仅沟通 list as triage needs it.

    The port is cut at the list and not at the driver, so a run holds cards as data
    and can reach nothing else. 仅沟通 List Recovery, card extraction and descriptor
    parsing stay inside the page object behind this (ADR 0016).
    """

    def is_on_list(self) -> bool:
        """Whether the list is already showing — the cheap probe triage narrates from."""

    def ensure_open_list(self) -> bool:
        """Navigate to the list from wherever the app is; True once it is showing."""

    def scroll_to_top(self) -> bool:
        """Reset the list to its newest messages by double-tapping the 消息 tab.

        The run calls this on entry and narrates the return value, so False has to
        mean "the device was not touched" rather than "something was attempted".
        """

    def visible_cards(self, max_items: int) -> list[CommunicationCard]:
        """The opening screen's cards, newest first, capped at ``max_items``."""

    def open_card(self, card: CommunicationCard) -> bool:
        """Open the conversation behind one card."""

    def mark_disinterest(self) -> bool:
        """Submit 不感兴趣 for the open conversation; the platform drops it from the list."""

    def confirm_back_on_list(self) -> bool:
        """Confirm the platform landed back on the list after an acknowledgment."""

    def has_message_tab_unread_dot(self) -> bool:
        """Check whether the bottom navigation 消息 tab shows an unread red dot."""

    def get_unread_badge_count(self) -> int | None:
        """The unread count badge on the 仅沟通 sub-tab; None when absent."""

    def scroll_list_down(self) -> bool:
        """Perform a single humanized scroll down on the conversation list."""


@runtime_checkable
class ChatActor(Protocol):
    """The open conversation as triage needs it."""

    def send_reply(self, text: str) -> bool:
        """Type and send one message into the open conversation."""

    def back_to_list(self) -> bool:
        """Leave the conversation without sending anything."""


@dataclass(frozen=True)
class TriagePages:
    """The device collaborators one triage run drives, already adapted to the ports.

    Composed at the edge (``boss_agent.adapters.outbound.device.pages.chat_bundle``)
    rather than reached for,
    so a caller — or a test — can hand a run a scripted screen and chat without
    patching module globals and without core constructing a page object.
    """

    list_reader: ChatListReader
    chat_actor: ChatActor
