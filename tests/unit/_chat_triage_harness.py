"""
tests.unit._chat_triage_harness
===============================
Scripted 仅沟通 device world for the triage suites.

``Harness`` stands in for the communication-list page object and ``FakeChatPage``
for the chat page object: both are what the production adapters wrap, so a test
drives a run through the same seam production uses and asserts on outcomes — which
companies were blacklisted, which cards were touched — never on internal calls.

The fixtures at the bottom are imported by the suites that need them, so a scripted
run never reads or writes the repo's own configuration.
"""

from pathlib import Path
from typing import Any

from boss_agent.chat_triage import (
    MAX_INSPECTED_CARDS,
    ChatActorAdapter,
    ChatTriage,
    CommunicationListAdapter,
)
from boss_agent.models import ScreeningPolicy
from boss_agent.pages import CommunicationCard
from boss_agent.rejection import (
    DISINTEREST_REASON,
    ChatAcknowledgmentSettings,
    RejectionVerdict,
)
from boss_agent.worker.handlers.check_chat import CheckChatPages

REJECTION_TEXT = "我们感谢您的投递，但您的专业技能与我们目前的职位需求并不完全吻合。"
INVITATION_TEXT = "您好，方便约个时间聊聊吗？"
DELIVERED_TEXT = "我对这个岗位很感兴趣，期待您的回复~"

COMPANY = "传音控股"
DESCRIPTOR = f"{COMPANY} | 算法工程师"


class FakeClassifier:
    """Deterministic stand-in for the LLM rejection judge."""

    def __init__(self, verdicts: dict[str, bool] | None = None, default: bool = False) -> None:
        self.verdicts = verdicts or {}
        self.default = default
        self.calls: list[tuple[str, str]] = []

    def classify(self, message_text: str, sender_name: str = "") -> RejectionVerdict:
        self.calls.append((sender_name, message_text))
        is_rejection = self.verdicts.get(message_text, self.default)
        return RejectionVerdict(
            is_rejection=is_rejection,
            confidence=0.9,
            rationale="rejection" if is_rejection else "positive",
        )


def card(
    text: str,
    *,
    sender: str = "招聘者",
    status: str = "",
    descriptor: str = "",
) -> CommunicationCard:
    """Build one 仅沟通 card: an inbound rejection unless a badge says otherwise."""
    return CommunicationCard(
        sender_name=sender,
        message_text=text,
        outbound_status=status,
        company_position=descriptor,
        row_text=f"{sender}\n{text}",
    )


class Harness:
    """In-memory 仅沟通 screen + chat simulator recording every write action.

    ``viewport_size`` is the whole list one screen can show: a run never scrolls, so
    it is exactly how many cards a pass can reach.
    """

    def __init__(
        self,
        cards: list[CommunicationCard],
        *,
        viewport_size: int = 2,
        on_list: bool = True,
        open_list_ok: bool = True,
        mark_disinterest_ok: bool = True,
        return_to_list_ok: bool = True,
        open_message_ok: bool = True,
    ) -> None:
        self.cards = cards
        self.viewport_size = viewport_size
        self.removed: set[str] = set()
        self.on_list = on_list
        self.open_list_ok = open_list_ok
        self.mark_disinterest_ok = mark_disinterest_ok
        self.return_to_list_ok = return_to_list_ok
        self.open_message_ok = open_message_ok
        self.events: list[str] = []

    # --- CommunicationListPage contract -----------------------------------------
    def is_on_list(self, timeout_sec: float = 2.0) -> bool:
        return self.on_list

    def open_list(self, timeout_sec: float = 5.0) -> bool:
        self.on_list = self.open_list_ok
        return self.open_list_ok

    def extract_visible_messages(self, max_items: int = 10) -> list[CommunicationCard]:
        visible = [m for m in self.cards if m.key not in self.removed]
        return visible[: self.viewport_size][:max_items]

    def open_message(self, message: CommunicationCard) -> bool:
        if not self.open_message_ok:
            return False
        self.events.append(f"open:{message.message_text}")
        return True

    def mark_disinterest(self, timeout_sec: float = 3.0) -> bool:
        if not self.mark_disinterest_ok:
            self.events.append("disinterest:failed")
            return False
        self.events.append(f"disinterest:{DISINTEREST_REASON}")
        # The platform drops the conversation from the list on success.
        for message in self.cards:
            if message.message_text in self.currently_opened:
                self.removed.add(message.key)
        return True

    def wait_for_list_return(self, timeout_sec: float = 5.0) -> bool:
        self.events.append("list_return")
        if self.return_to_list_ok:
            self.cards = [m for m in self.cards if m.key not in self.removed]
        return self.return_to_list_ok

    # --- test helpers -----------------------------------------------------------
    @property
    def currently_opened(self) -> set[str]:
        return {m.message_text for m in self.cards if f"open:{m.message_text}" in self.events}


class EndlessOutboundHarness(Harness):
    """A screen that keeps yielding fresh outbound cards, and never settles.

    Skipping an outbound card is free, so neither the LLM budget nor the screen's
    width can bound a run against it: the terminal safety ceiling is the only exit.
    """

    def __init__(self) -> None:
        super().__init__([], viewport_size=10)
        self.reads = 0

    def extract_visible_messages(self, max_items: int = 10) -> list[CommunicationCard]:
        self.reads += 1
        return [
            card(DELIVERED_TEXT, sender=f"招聘者{self.reads}-{i}", status="[送达]")
            for i in range(max_items)
        ]


class FakeChatPage:
    def __init__(self, harness: Harness) -> None:
        self.harness = harness

    def send_message(self, message: str, timeout_sec: float = 5.0) -> bool:
        self.harness.events.append(f"send:{message}")
        return True

    def navigate_back(self, timeout_sec: float = 3.0) -> bool:
        self.harness.events.append("chat_back")
        return True


def triage_run(
    harness: Harness,
    *,
    classifier: Any = None,
    policy: ScreeningPolicy | None = None,
    settings: ChatAcknowledgmentSettings | None = None,
    log: Any = None,
    is_cancelled: Any = None,
    max_inspected_cards: int = MAX_INSPECTED_CARDS,
) -> ChatTriage:
    """One triage run over the scripted device world, through the production adapters."""
    return ChatTriage(
        list_reader=CommunicationListAdapter(harness),
        chat_actor=ChatActorAdapter(FakeChatPage(harness)),
        classifier=classifier or FakeClassifier(),
        policy=policy or ScreeningPolicy(),
        settings=settings or ChatAcknowledgmentSettings(),
        log=log,
        is_cancelled=is_cancelled,
        max_inspected_cards=max_inspected_cards,
    )


def scripted_pages(harness: Harness, chat_page: Any | None = None) -> Any:
    """The device world a CHECK_CHAT dispatch drives: the harness for both pages."""
    chat = chat_page if chat_page is not None else FakeChatPage(harness)
    return lambda driver: CheckChatPages(list_page=harness, chat_page=chat)


def recording_log() -> tuple[list[str], Any]:
    """A log sink and the lines it collected, for asserting on what a run narrated."""
    lines: list[str] = []

    async def log(line: str) -> None:
        lines.append(line)

    return lines, log


def throwaway_config(path: Path) -> Path:
    """A config file a run may write: a real run must never touch the repo's own."""
    path.write_text(
        "# 公司黑名单（一票否决，受直招保护守卫约束）\ncompany_blacklist: []\n",
        encoding="utf-8",
    )
    return path
