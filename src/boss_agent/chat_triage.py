"""
boss_agent.chat_triage
======================
Deep Chat Triage module (Spec #267, ADR 0013's chat half).

One engine owns the whole 仅沟通 rejection story: the two-tier zero-unread preflight,
the Unread-Badge Bounded Scan, the stop-reason taxonomy, the zero-token Outbound
Message Indicator bypass, the classify → guardrail → blacklist-ingest → acknowledge
ordering, dry-run, and the per-run tallies. Its interface is one verb — ``scan()`` —
returning a Triage Report. The CHECK_CHAT handler configures a run and maps that
report into task telemetry; it never touches a card, a page object or a device.

A run opens with a preflight that can end it before any card is read: the bottom
bar's 消息 dot first, then the 仅沟通 badge behind it. An absent probe means one of two
very different things — a clean account, or a screen that has not finished rendering —
and only one of them justifies skipping the scan, so an absence is believed only once
it survives a settle window (``UNREAD_ABSENCE_CONFIRMATIONS`` readings). Anything less
falls through to the scan, where a wrong guess costs nothing but time.

Past the preflight the scan is bounded by the platform's own unread badge (ADR 0017).
It reads the opening viewport, re-reads the screen after each acknowledgment, and
while the 仅沟通 badge still counts unread messages it pages downwards with humanized
swipes — so a rejection buried under a stack of outbound-waiting threads is reached.
The list is ordered newest-first and drops a conversation from view when it is marked
不感兴趣, so the top of the screen is both where new state arrives and where the list
drains from. A run stops when the badge clears, when the list stops yielding new
cards, or at one of its ceilings (``max_scroll_swipes``, ``max_scan_depth`` for LLM
evaluations, and ``MAX_INSPECTED_CARDS`` as the loop's termination guarantee), and
every exit names its ``stop_reason``.

Paging had been tried twice and removed (Issues #207, #239, ADRs 0011 and 0014):
nothing bounded a walk that kept meeting fresh zero-token outbound cards and
never-removed preserved ones, so a run could walk the whole backlog on every dispatch.
ADR 0015 responded by reading the opening screen and nothing else, which blinded the
scan to unreads pushed below the fold; ADR 0017 restores paging with the one bound
that was missing — the unread badge the platform already maintains.

The device world crosses two narrow ports — a list reader and a chat actor — and
cards cross as data with an opaque handle, never a device reference. The production
adapters wrap the existing page objects, which keep owning 仅沟通 List Recovery, card
extraction and descriptor parsing (ADR 0016 is not revisited).
"""

import asyncio
import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from .feed_pipeline import is_task_cancelled
from .pages import (
    OUTBOUND_STATUS_MARKERS,
    ChatPage,
    CommunicationCard,
    CommunicationListPage,
    StartupDialogPage,
)
from .rejection import DEFAULT_MAX_SCROLL_SWIPES, ChatAcknowledgmentSettings
from .screening_policy import ScreeningPolicy

logger = logging.getLogger(__name__)

#: Cards read from the opening screen per pass. Also the scan's width: the run never
#: scrolls, so this bounds how much of the list one pass can reach.
VIEWPORT_SIZE = 10

#: Hard ceiling on cards inspected in one run. `max_scan_depth` bounds LLM
#: evaluations, and an outbound card costs none — so on its own it cannot stop a
#: screen that keeps yielding fresh outbound cards as acknowledged ones leave it.
#: This is a safety bound, not a tuning knob: it exists so the scan loop always
#: terminates. It is set to the traversal's own reach — `max_scroll_swipes` (5)
#: viewports past the opening screen, at `VIEWPORT_SIZE` (10) cards each — so it
#: never truncates a run the swipe ceiling would have allowed (ADR 0017).
MAX_INSPECTED_CARDS = 50

#: Characters of message text echoed into task logs.
PREVIEW_CHARS = 40

#: Timeout budget for a single list/dialog interaction.
PAGE_TIMEOUT_SEC = 5.0

#: Timeout budget for the cheap "is the list already showing" probe.
LIST_PROBE_TIMEOUT_SEC = 1.0

#: Readings an absent unread probe must survive before a run calls the account clean.
#: A zero-unread preflight is a *skip*, so one absent reading cannot carry it: on a
#: cold start the bottom bar renders before its badge state is populated, and a list
#: that has only just appeared has not drawn its 仅沟通 count yet. Either would
#: otherwise be read as a clean account and swallow real unreads.
UNREAD_ABSENCE_CONFIRMATIONS = 3

#: Wall-clock gap between those readings, so a render that is merely late is not
#: mistaken for an absent probe.
SETTLE_PAUSE_SEC = 0.35

#: Narration for a feed reset the device actually performed. Named so a test asserts
#: on the same string the run emits rather than a re-spelled copy of it.
LIST_RESET_LOG = "🔝 [Navigation] 双击「消息」导航按钮，快速回到最新消息列表顶部"

#: Narration for a reset the page declined — the 消息 tab was not on screen, so no
#: gesture was made. A run that could not reset must not narrate the success line.
LIST_RESET_FAILED_LOG = (
    "⚠️ [Navigation] 「消息」导航栏不可用，未能双击回到最新消息列表顶部；"
    "列表可能停留在历史位置，继续扫描"
)


class StopReason(StrEnum):
    """Why a triage run stopped.

    The first seven are the scan's own exits. ``LIST_UNREACHABLE`` is the entry
    failure that precedes them: the list could not be opened, so no card was read.
    """

    UNREAD_CLEARED = "unread_cleared"
    EMPTY_LIST = "empty_list"
    CANCELLED = "cancelled"
    MAX_SCAN_DEPTH = "max_scan_depth"
    SCAN_CEILING = "scan_ceiling"
    SCROLL_CEILING = "scroll_ceiling"
    LOST_LIST = "lost_list"
    LIST_UNREACHABLE = "list_unreachable"


class TriageKind(StrEnum):
    """What triage did with one communication card."""

    #: Outbound Message Indicator present: waiting on the recruiter, never touched.
    SKIPPED = "skipped"
    PRESERVED = "preserved"
    ACKNOWLEDGED = "acknowledged"
    DRY_RUN = "dry_run"
    FAILED = "failed"


@dataclass(frozen=True)
class TriageOutcome:
    """Result of triaging one card, tallied into the run's report."""

    kind: TriageKind
    is_rejection: bool = False
    #: Set when the employer was newly added to the company blacklist.
    blacklisted: bool = False
    #: Set when a guardrail (headhunter agency / masked name) refused the employer.
    guardrail_blocked: bool = False
    navigated: bool = False
    #: Set when the platform never returned to the list; the scan must stop rather
    #: than read cards off an unknown screen.
    lost_list: bool = False


@dataclass(frozen=True)
class TriageReport:
    """What one triage run found and did, for the caller's task telemetry."""

    stop_reason: StopReason
    dry_run: bool = False
    scanned: int = 0
    evaluated: int = 0
    skipped_outbound: int = 0
    acknowledged: int = 0
    preserved: int = 0
    failed: int = 0
    rejections: int = 0
    guardrail_blocked: int = 0
    blacklisted_companies: tuple[str, ...] = ()
    visited_keys: frozenset[str] = frozenset()
    scroll_swipes: int = 0

    @property
    def blacklisted_count(self) -> int:
        """How many companies this run newly added to the blacklist."""
        return len(self.blacklisted_companies)

    def summary_line(self) -> str:
        """The operator-facing summary of this run, rendered by the dashboard."""
        note = f" [{', '.join(self.blacklisted_companies)}]" if self.blacklisted_companies else ""
        return (
            f"Finished CHECK_CHAT: scanned {self.scanned} card(s), "
            f"scrolled {self.scroll_swipes} swipe(s), "
            f"evaluated {self.evaluated} message(s), "
            f"{self.skipped_outbound} skipped as outbound, "
            f"{self.rejections} rejection(s) detected, "
            f"{self.blacklisted_count} company(ies) blacklisted{note}, "
            f"{self.guardrail_blocked} blocked by guardrails, "
            f"{self.acknowledged} acknowledged, "
            f"{self.preserved} preserved, "
            f"{self.failed} failed "
            f"(stop_reason={self.stop_reason.value}, dry_run={self.dry_run})"
        )


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
    """The device collaborators one triage run drives, composed from its driver.

    Composed rather than reached for, so a caller — or a test — can hand a run a
    scripted screen and chat without patching module globals.
    """

    list_page: Any
    chat_page: Any

    @classmethod
    def for_driver(cls, driver: Any) -> "TriagePages":
        """Compose the production device world for ``driver``.

        The startup dialog is dismissed here, before any page object is handed to a
        run: a dispatch can land on it, and a dialog left up would swallow the clicks
        the run is about to make.
        """
        startup_page = StartupDialogPage(driver)
        if startup_page.is_dialog_present():
            startup_page.dismiss_dialog()
        return cls(list_page=CommunicationListPage(driver), chat_page=ChatPage(driver))


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


class ChatTriage:
    """One 仅沟通 rejection-triage run.

    Dependencies are injected so a run can be driven against synthetic screen state:
    two ports for the device world, a rejection classifier and the Screening Policy
    for every judgement, a log sink and a cancellation probe for the task plumbing.
    The module holds no broker and no driver.
    """

    def __init__(
        self,
        *,
        list_reader: ChatListReader,
        chat_actor: ChatActor,
        classifier: Any,
        policy: ScreeningPolicy,
        settings: ChatAcknowledgmentSettings,
        log: Callable[[str], Awaitable[None]] | None = None,
        is_cancelled: Callable[[], Awaitable[bool]] | None = None,
        pause: Callable[[float], Awaitable[None]] | None = None,
        max_inspected_cards: int | None = None,
    ) -> None:
        self.list_reader = list_reader
        self.chat_actor = chat_actor
        self.classifier = classifier
        self.policy = policy
        self.settings = settings
        if max_inspected_cards is not None:
            self.max_inspected_cards = max_inspected_cards
        elif self.settings.max_scroll_swipes > DEFAULT_MAX_SCROLL_SWIPES:
            self.max_inspected_cards = (self.settings.max_scroll_swipes + 1) * VIEWPORT_SIZE
        else:
            self.max_inspected_cards = MAX_INSPECTED_CARDS
        self._log_sink = log
        self._cancel_probe = is_cancelled
        # Injected so a test drives the settle window without waiting it out.
        self._pause = pause or asyncio.sleep

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------
    @classmethod
    def for_task(
        cls,
        broker: Any,
        task_id: str,
        driver: Any,
        *,
        classifier: Any,
        policy: ScreeningPolicy,
        settings: ChatAcknowledgmentSettings,
        pages: Any = None,
    ) -> "ChatTriage":
        """Compose a run for a worker task: its device world, log sink and cancel probe.

        Task plumbing lives here, so the module stays free of broker knowledge, and so
        does the composition of the device world — the same shape the Mobile Job Feed
        Pipeline composes its runs with. ``pages`` overrides how the driver becomes the
        two page objects, which is how a test scripts the screen.
        """
        device = (pages or TriagePages.for_driver)(driver)
        return cls(
            list_reader=CommunicationListAdapter(device.list_page),
            chat_actor=ChatActorAdapter(device.chat_page),
            classifier=classifier,
            policy=policy,
            settings=settings,
            log=lambda line: broker.append_log(task_id, line),
            is_cancelled=lambda: is_task_cancelled(broker, task_id),
        )

    # ------------------------------------------------------------------
    # Unread probes
    # ------------------------------------------------------------------
    def _message_tab_dot_reads_clear(self) -> bool:
        """One reading of the bottom 消息 tab's unread dot: True when no dot is shown.

        A bar that is not on screen at all reads as *not* clear rather than clear: the
        probe can conclude nothing about an account whose navigation is not visible, so
        the run falls through to the list instead of skipping it.
        """
        return not self.list_reader.has_message_tab_unread_dot()

    def _unread_badge_reads_clear(self) -> bool:
        """One reading of the 仅沟通 badge: True when it is absent or counts zero."""
        count = self.list_reader.get_unread_badge_count()
        return count is None or count <= 0

    async def _confirm_clear(self, reads_clear: Callable[[], bool]) -> bool:
        """Whether "no unread" holds across a settle window of repeated readings.

        ``UNREAD_ABSENCE_CONFIRMATIONS`` readings spaced by ``SETTLE_PAUSE_SEC``, so a
        badge that merely has not been drawn yet cannot pass for a cleared one. A single
        positive reading fails the confirmation immediately — the run is only ever
        slowed down when the probe is about to make it skip work.
        """
        for reading in range(UNREAD_ABSENCE_CONFIRMATIONS):
            if not reads_clear():
                return False
            if reading + 1 < UNREAD_ABSENCE_CONFIRMATIONS:
                await self._pause(SETTLE_PAUSE_SEC)
        return True

    async def _badge_after_confirming_clear(self) -> int | None:
        """The 仅沟通 unread count, or None once an absent badge has been confirmed.

        A positive reading comes straight back — the common case while unreads remain,
        and it costs no extra probe. A zero count or an absent badge is re-read across
        the settle window first, so only a *confirmed* absence reports as ``None``; a
        count that simply had not rendered yet is returned as itself and the run keeps
        scanning rather than skipping the unreads it was about to find.
        """
        count = self.list_reader.get_unread_badge_count()
        if count is not None and count > 0:
            return count
        if await self._confirm_clear(self._unread_badge_reads_clear):
            return None
        return self.list_reader.get_unread_badge_count()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    async def scan(self) -> TriageReport:
        """Run an Unread-Badge Bounded Scan and report what it found and did."""
        # Tier 1 Preflight: inspect bottom tab red dot if bottom nav is visible
        if await self._confirm_clear(self._message_tab_dot_reads_clear):
            await self._log(
                "🔔 [Preflight Tier 1] 底栏「消息」无未读红点（已连续确认 "
                f"{UNREAD_ABSENCE_CONFIRMATIONS} 次），零点击零卡片瞬时放行"
                "（stop_reason=unread_cleared）"
            )
            return TriageReport(
                stop_reason=StopReason.UNREAD_CLEARED, dry_run=self.settings.dry_run
            )

        if not await self._enter_list():
            return TriageReport(
                stop_reason=StopReason.LIST_UNREACHABLE, dry_run=self.settings.dry_run
            )

        # Tier 2 Preflight: check 仅沟通 category sub-tab unread badge count
        badge_count = await self._badge_after_confirming_clear()
        if badge_count is None:
            await self._log(
                "🔔 [Preflight Tier 2] 「仅沟通」无未读角标（红点由其他分类引起），无需处理卡片，"
                "瞬时放行（stop_reason=unread_cleared）"
            )
            return TriageReport(
                stop_reason=StopReason.UNREAD_CLEARED, dry_run=self.settings.dry_run
            )

        await self._log(
            f"📥 [List] 已进入「仅沟通」列表，当前未读角标: {badge_count}，"
            f"启动未读角标驱动扫描（max_swipes={self.settings.max_scroll_swipes}）"
        )

        counters: Counter[TriageKind] = Counter()
        visited_keys: set[str] = set()
        blacklisted: list[str] = []
        evaluated = scanned = rejections = guardrail_blocked = 0
        scroll_swipes = 0
        consecutive_empty_scrolls = 0
        stop_reason: StopReason | None = None

        while True:
            if await self._is_cancelled():
                stop_reason = StopReason.CANCELLED
                await self._log("🛑 [Task Cancelled] 任务已被取消，终止列表扫描")
                break

            visible = self.list_reader.visible_cards(VIEWPORT_SIZE)
            if not visible:
                stop_reason = StopReason.EMPTY_LIST
                break

            pending = [card for card in visible if card.key not in visited_keys]
            if not pending:
                # All visible cards in current viewport have been visited.
                current_badge = await self._badge_after_confirming_clear()
                if current_badge is None:
                    stop_reason = StopReason.UNREAD_CLEARED
                    await self._log(
                        "🎉 [Unread Cleared] 「仅沟通」未读角标已清零，未读消息处理完毕，扫描结束"
                        f"（stop_reason={stop_reason.value}）"
                    )
                    break

                if scroll_swipes >= self.settings.max_scroll_swipes:
                    stop_reason = StopReason.SCROLL_CEILING
                    await self._log(
                        f"🛑 [Scroll Ceiling] 滑动翻页已达上限 {self.settings.max_scroll_swipes} 次，"
                        f"仍有未读角标 ({current_badge})，终止扫描"
                    )
                    break

                if scanned >= self.max_inspected_cards:
                    # The card ceiling is the scan's own bound, so it reports as one
                    # whatever the swipe count happens to be: naming it a scroll
                    # ceiling would tell an operator the swipe limit had fired when it
                    # had not, and leave the log tag contradicting `stop_reason`.
                    stop_reason = StopReason.SCAN_CEILING
                    await self._log(
                        f"🛑 [Scan Ceiling] 单次扫描已达 {self.max_inspected_cards} 张卡片上限，"
                        "终止扫描"
                    )
                    break

                scroll_swipes += 1
                await self._log(
                    f"📜 [Scroll] 当前屏幕卡片已遍历完毕，未读角标仍存在 ({current_badge})，"
                    f"向下滑动翻页第 {scroll_swipes}/{self.settings.max_scroll_swipes} 次"
                )
                self.list_reader.scroll_list_down()

                new_visible = self.list_reader.visible_cards(VIEWPORT_SIZE)
                new_keys = {c.key for c in new_visible} - visited_keys
                if not new_keys:
                    consecutive_empty_scrolls += 1
                    if consecutive_empty_scrolls >= 2:
                        stop_reason = StopReason.SCROLL_CEILING
                        await self._log(
                            "🛑 [Scroll Ceiling] 连续 2 次滑动未发现新卡片（列表已触底），终止扫描"
                        )
                        break
                else:
                    consecutive_empty_scrolls = 0
                continue

            lost_list = False
            for card in pending:
                if evaluated >= self.settings.max_scan_depth or scanned >= self.max_inspected_cards:
                    break
                visited_keys.add(card.key)
                scanned += 1
                outcome = await self._triage_card(card)
                counters[outcome.kind] += 1
                if outcome.kind is not TriageKind.SKIPPED:
                    evaluated += 1
                    rejections += int(outcome.is_rejection)
                if outcome.blacklisted and card.company_name:
                    blacklisted.append(card.company_name)
                guardrail_blocked += int(outcome.guardrail_blocked)
                if outcome.navigated:
                    # The platform shifted the remaining cards up: abandon this
                    # screen snapshot and re-read before continuing.
                    lost_list = outcome.lost_list
                    if not lost_list:
                        badge_after = await self._badge_after_confirming_clear()
                        if badge_after is None:
                            stop_reason = StopReason.UNREAD_CLEARED
                            await self._log(
                                "🎉 [Unread Cleared] 会话处理后「仅沟通」未读角标已清零，"
                                f"未读消息处理完毕，扫描结束（stop_reason={stop_reason.value}）"
                            )
                            break
                    break

            if stop_reason is not None:
                break
            if lost_list:
                # We are no longer looking at the list; reading cards here could
                # interact with an unrelated screen, so stop instead.
                stop_reason = StopReason.LOST_LIST
                break
            if evaluated >= self.settings.max_scan_depth:
                stop_reason = StopReason.MAX_SCAN_DEPTH
                break
            if scanned >= self.max_inspected_cards:
                stop_reason = StopReason.SCAN_CEILING
                await self._log(
                    f"🛑 [Scan Ceiling] 单次扫描已达 {self.max_inspected_cards} 张卡片上限，"
                    "终止列表扫描"
                )
                break
            # Loop back for another read of the same screen: an acknowledged card leaves
            # the list, and the cards under it move up into the space it freed.
            # `visited_keys` is what keeps a preserved card from being judged twice.

        assert stop_reason is not None, "every scan exit names its stop reason"
        return TriageReport(
            stop_reason=stop_reason,
            dry_run=self.settings.dry_run,
            scanned=scanned,
            evaluated=evaluated,
            skipped_outbound=counters[TriageKind.SKIPPED],
            acknowledged=counters[TriageKind.ACKNOWLEDGED],
            preserved=counters[TriageKind.PRESERVED],
            failed=counters[TriageKind.FAILED],
            rejections=rejections,
            guardrail_blocked=guardrail_blocked,
            blacklisted_companies=tuple(blacklisted),
            visited_keys=frozenset(visited_keys),
            scroll_swipes=scroll_swipes,
        )

    # ------------------------------------------------------------------
    # Scan entry
    # ------------------------------------------------------------------
    async def _enter_list(self) -> bool:
        """Bring the 仅沟通 list to the front, narrating the recovery it needed."""
        # A dispatch can land while the app sits on a job detail, an open chat, a
        # filter sheet, or an alternate chat tab (e.g. 有交换, 新招呼). Because page source
        # cannot differentiate which sub-tab is currently active, ensure_open_list()
        # explicitly clicks 消息 -> 仅沟通 rather than assuming presence equals selection.
        await self._log("🔄 [Navigation] 启动自愈导航返回「消息」栏目并进入「仅沟通」列表")
        if not self.list_reader.ensure_open_list():
            await self._log("❌ [List] 无法进入「仅沟通」列表，任务终止")
            return False
        # Landing on 仅沟通 is not landing on its newest messages: the feed is shared
        # state that survives navigation, so the run resets it with a double-tap on
        # 消息 (#388). The reset is a step of its own rather than a side effect of
        # navigation because its outcome has to reach the narration: the page
        # declines it outright when the 消息 tab is not on screen, and a run that
        # claimed a reset it could not perform would be describing a device action
        # that never happened. A declined reset is not a navigation failure — the
        # list is showing — so the scan continues, from wherever the feed stands.
        if self.list_reader.scroll_to_top():
            await self._log(LIST_RESET_LOG)
        else:
            await self._log(LIST_RESET_FAILED_LOG)
        return True

    # ------------------------------------------------------------------
    # Per-card stages
    # ------------------------------------------------------------------
    async def _triage_card(self, card: CommunicationCard) -> TriageOutcome:
        """Classify one card and act on it when it is an explicit rejection."""
        sender = _sender(card)

        if card.has_outbound_indicator:
            desc = "存在未发送草稿" if "草稿" in card.outbound_status else "我发出后对方未回复"
            await self._log(
                f"⏭️ [出站等待·零Token] '{sender}': {desc}"
                f"（{card.outbound_status}），跳过，零 Token 消耗"
            )
            return TriageOutcome(kind=TriageKind.SKIPPED)

        if card.outbound_status.strip():
            # An unrecognised badge is evaluated rather than skipped, so a wording
            # change on the platform can never silently disable rejection triage.
            await self._log(
                f"⚠️ [未知状态标签] '{sender}': '{card.outbound_status}' 不匹配已知出站标记"
                f"（{'/'.join(OUTBOUND_STATUS_MARKERS)}），按入站消息继续判定"
            )

        verdict = self.classifier.classify(card.message_text, card.sender_name)
        preview = _preview(card.message_text)

        if not verdict.is_rejection:
            note = f"（判定异常: {verdict.error}）" if verdict.error else ""
            await self._log(f"⏭️ [正常消息·LLM判定] '{sender}': {preview} {note}— 保留，不处理")
            return TriageOutcome(kind=TriageKind.PRESERVED)

        await self._log(
            f"🎯 [拒信命中] '{sender}'"
            f"{f' ({card.company_position})' if card.company_position else ''}"
            f": {preview} (置信度 {verdict.confidence:.2f}, "
            f"依据: {verdict.rationale or '未说明'})"
        )

        blacklisted, guardrail_blocked = await self._ingest_blacklist(card)

        if self.settings.dry_run:
            await self._log(
                f"🧪 [DRY-RUN] 拟回复 '{self.settings.rejection_reply_text}' "
                "并将该会话标记为不感兴趣（重复推荐），本次演练不执行任何实际操作",
            )
            return TriageOutcome(
                kind=TriageKind.DRY_RUN,
                is_rejection=True,
                blacklisted=blacklisted,
                guardrail_blocked=guardrail_blocked,
            )

        outcome = await self._acknowledge(card)
        return replace(
            outcome,
            is_rejection=True,
            blacklisted=blacklisted,
            guardrail_blocked=guardrail_blocked,
        )

    async def _ingest_blacklist(self, card: CommunicationCard) -> tuple[bool, bool]:
        """Add the card's employer to the company blacklist and persist it.

        Returns (newly_blacklisted, guardrail_blocked). Never let a missing employer
        or a guardrail refusal abort the acknowledgment: the rejection is still worth
        closing politely.
        """
        sender = _sender(card)
        company = card.company_name
        if not company:
            await self._log(
                f"⚠️ [企业解析] 无法从 '{sender}' 的卡片解析出企业名"
                f"（{card.company_position or '无 [公司] | [岗位] 描述'}），跳过拉黑"
            )
            return False, False

        already_listed = company in self.policy.company_blacklist

        if self.settings.dry_run:
            allowed, notice = self.policy.validate_can_blacklist_company(company)
            if allowed:
                await self._log(
                    f"🧪 [DRY-RUN] 拟将企业 '{company}' 加入公司黑名单并写入筛选配置，"
                    "本次演练不修改任何文件",
                )
                return False, False
            await self._log(f"🧪 [DRY-RUN] {notice}")
            return False, True

        added, notice = self.policy.add_company_to_blacklist(company)
        if not added:
            await self._log(f"🛡️ {notice}")
            return False, True

        if already_listed:
            await self._log(f"ℹ️ 企业 '{company}' 已在公司黑名单中，无需重复写入")
            return False, False

        try:
            written = self.policy.persist_company_blacklist(company)
        except Exception as exc:  # noqa: BLE001 - the in-memory policy still holds
            await self._log(f"⚠️ [持久化] 企业 '{company}' 已加入内存黑名单，但写入配置失败: {exc}")
            return True, False

        if written is None:
            await self._log(
                f"⚠️ [持久化] 企业 '{company}' 已加入内存黑名单，但 "
                f"{self.policy.source_path or '筛选配置'} 无法安全写入，本次未落盘",
            )
            return True, False

        await self._log(f"🚫 [黑名单] 已将企业 '{company}' 加入公司黑名单并写入 {written}")
        return True, False

    async def _acknowledge(self, card: CommunicationCard) -> TriageOutcome:
        """Open the chat, send the polite reply, and submit disinterest feedback."""
        sender = _sender(card)
        if not self.list_reader.open_card(card):
            await self._log(f"❌ [Chat Open Error] 无法打开与 '{sender}' 的会话，已跳过")
            return TriageOutcome(kind=TriageKind.FAILED)

        if not self.chat_actor.send_reply(self.settings.rejection_reply_text):
            await self._log(f"❌ [Send Error] 向 '{sender}' 发送礼貌回复失败，回退到列表")
            self.chat_actor.back_to_list()
            return TriageOutcome(kind=TriageKind.FAILED, navigated=True)

        await self._log(
            f"✅ [Reply Sent] 已向 '{sender}' 发送 '{self.settings.rejection_reply_text}'"
        )

        if not self.list_reader.mark_disinterest():
            await self._log(
                "❌ [不感兴趣 Error] “不感兴趣”入口或“重复推荐”选项未出现，"
                "已放弃本次标记并回退到列表",
            )
            self.chat_actor.back_to_list()
            return TriageOutcome(kind=TriageKind.FAILED, navigated=True)

        await self._log(f"🗑️ [Disinterest] 已将 '{sender}' 标记为不感兴趣（重复推荐）")

        if not self.list_reader.confirm_back_on_list():
            await self._log(
                "⚠️ [Navigation] 标记后未能确认返回列表；为避免在未知页面上误操作，终止本次扫描",
            )
            return TriageOutcome(kind=TriageKind.ACKNOWLEDGED, navigated=True, lost_list=True)

        return TriageOutcome(kind=TriageKind.ACKNOWLEDGED, navigated=True)

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------
    async def _log(self, message: str) -> None:
        logger.info(message)
        if self._log_sink is not None:
            await self._log_sink(message)

    async def _is_cancelled(self) -> bool:
        if self._cancel_probe is None:
            return False
        return await self._cancel_probe()


def _sender(card: CommunicationCard) -> str:
    """The name a card's recruiter is narrated by, when the card carries one."""
    return card.sender_name or "未知招聘者"


def _preview(text: str) -> str:
    flat = " ".join((text or "").split())
    if len(flat) <= PREVIEW_CHARS:
        return flat
    return flat[:PREVIEW_CHARS] + "…"


__all__ = [
    "ChatActor",
    "ChatActorAdapter",
    "ChatListReader",
    "ChatTriage",
    "CommunicationListAdapter",
    "StopReason",
    "TriageKind",
    "TriagePages",
    "TriageOutcome",
    "TriageReport",
]
