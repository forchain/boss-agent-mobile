"""
boss_agent.chat_triage
======================
Deep Chat Triage module (Spec #267, ADR 0013's chat half).

One engine owns the whole 仅沟通 rejection story: the bounded First-Screen Scan,
the stop-reason taxonomy, the zero-token Outbound Message Indicator bypass, the
classify → guardrail → blacklist-ingest → acknowledge ordering, dry-run, and the
per-run tallies. Its interface is one verb — ``scan()`` — returning a Triage Report.
The CHECK_CHAT handler configures a run and maps that report into task telemetry;
it never touches a card, a page object or a device.

The scan reads the *opening screen* of the list and nothing else (ADR 0015). The
list is ordered newest-first, and marking a conversation 不感兴趣 drops it from the
list, so the top of the screen is both where new state arrives and where the list
drains from. A pass therefore judges what it can see, re-reads the screen after each
acknowledgment, and stops once nothing on it is new: no scrolling, no pagination,
and no per-run accumulation to reason about.

Paging was tried twice and removed. The list yields an unbounded run of zero-token
outbound cards and of preserved cards that are never removed, so neither a depth
bound nor a card ceiling nor a time cursor could keep a run from walking the whole
backlog on every dispatch (Issues #239, ADR 0014). The cost is coverage, and it is
deliberate: a card below the fold is reached only after the cards above it leave the
list. See ADR 0015.

The device world crosses two narrow ports — a list reader and a chat actor — and
cards cross as data with an opaque handle, never a device reference. The production
adapters wrap the existing page objects, which keep owning 仅沟通 List Recovery, card
extraction and descriptor parsing (ADR 0016 is not revisited).
"""

import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from .feed_pipeline import is_task_cancelled
from .models import ScreeningPolicy
from .pages import (
    OUTBOUND_STATUS_MARKERS,
    ChatPage,
    CommunicationCard,
    CommunicationListPage,
)
from .rejection import ChatAcknowledgmentSettings

logger = logging.getLogger(__name__)

#: Cards read from the opening screen per pass. Also the scan's width: the run never
#: scrolls, so this bounds how much of the list one pass can reach.
VIEWPORT_SIZE = 10

#: Hard ceiling on cards inspected in one run. `max_scan_depth` bounds LLM
#: evaluations, and an outbound card costs none — so on its own it cannot stop a
#: screen that keeps yielding fresh outbound cards as acknowledged ones leave it.
#: This is a safety bound, not a tuning knob: it exists so the scan loop always
#: terminates.
MAX_INSPECTED_CARDS = 300

#: Characters of message text echoed into task logs.
PREVIEW_CHARS = 40

#: Timeout budget for a single list/dialog interaction.
PAGE_TIMEOUT_SEC = 5.0

#: Timeout budget for the cheap "is the list already showing" probe.
LIST_PROBE_TIMEOUT_SEC = 1.0


class StopReason(StrEnum):
    """Why a triage run stopped.

    The first six are the scan's own exits. ``LIST_UNREACHABLE`` is the entry
    failure that precedes them: the list could not be opened, so no card was read.
    """

    FIRST_SCREEN_EXHAUSTED = "first_screen_exhausted"
    EMPTY_LIST = "empty_list"
    CANCELLED = "cancelled"
    MAX_SCAN_DEPTH = "max_scan_depth"
    SCAN_CEILING = "scan_ceiling"
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
    #: Cards judged as rejections while dry-running: logged, never acted on.
    rehearsed: int = 0
    rejections: int = 0
    guardrail_blocked: int = 0
    blacklisted_companies: tuple[str, ...] = ()
    visited_keys: frozenset[str] = frozenset()

    @property
    def blacklisted(self) -> int:
        """How many companies this run newly added to the blacklist."""
        return len(self.blacklisted_companies)

    def summary_line(self) -> str:
        """The operator-facing summary of this run, rendered by the dashboard."""
        note = f" [{', '.join(self.blacklisted_companies)}]" if self.blacklisted_companies else ""
        return (
            f"Finished CHECK_CHAT: scanned {self.scanned} card(s), "
            f"evaluated {self.evaluated} message(s), "
            f"{self.skipped_outbound} skipped as outbound, "
            f"{self.rejections} rejection(s) detected, "
            f"{self.blacklisted} company(ies) blacklisted{note}, "
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

    def visible_cards(self, max_items: int) -> list[CommunicationCard]:
        """The opening screen's cards, newest first, capped at ``max_items``."""

    def open_card(self, card: CommunicationCard) -> bool:
        """Open the conversation behind one card."""

    def mark_disinterest(self) -> bool:
        """Submit 不感兴趣 for the open conversation; the platform drops it from the list."""

    def confirm_back_on_list(self) -> bool:
        """Confirm the platform landed back on the list after an acknowledgment."""


@runtime_checkable
class ChatActor(Protocol):
    """The open conversation as triage needs it."""

    def send_reply(self, text: str) -> bool:
        """Type and send one message into the open conversation."""

    def back_to_list(self) -> bool:
        """Leave the conversation without sending anything."""


class CommunicationListAdapter:
    """The production list reader: the 仅沟通 page object, behind triage's vocabulary."""

    def __init__(self, page: CommunicationListPage, timeout_sec: float = PAGE_TIMEOUT_SEC) -> None:
        self._page = page
        self._timeout_sec = timeout_sec

    def is_on_list(self) -> bool:
        return self._page.is_on_list(timeout_sec=LIST_PROBE_TIMEOUT_SEC)

    def ensure_open_list(self) -> bool:
        return self._page.open_list(timeout_sec=self._timeout_sec)

    def visible_cards(self, max_items: int) -> list[CommunicationCard]:
        return self._page.extract_visible_messages(max_items=max_items)

    def open_card(self, card: CommunicationCard) -> bool:
        return self._page.open_message(card)

    def mark_disinterest(self) -> bool:
        return self._page.mark_disinterest(timeout_sec=self._timeout_sec)

    def confirm_back_on_list(self) -> bool:
        return self._page.wait_for_list_return(timeout_sec=self._timeout_sec)


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
        max_inspected_cards: int = MAX_INSPECTED_CARDS,
    ) -> None:
        self.list_reader = list_reader
        self.chat_actor = chat_actor
        self.classifier = classifier
        self.policy = policy
        self.settings = settings
        self.max_inspected_cards = max_inspected_cards
        self._log_sink = log
        self._cancel_probe = is_cancelled

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------
    @classmethod
    def for_task(
        cls,
        broker: Any,
        task_id: str,
        *,
        list_reader: ChatListReader,
        chat_actor: ChatActor,
        classifier: Any,
        policy: ScreeningPolicy,
        settings: ChatAcknowledgmentSettings,
    ) -> "ChatTriage":
        """Wire a triage run to a worker task: its log sink and cancellation probe.

        The device world arrives already adapted, so task plumbing lives here and
        the module stays free of broker knowledge — the same shape the Mobile Job
        Feed Pipeline composes its runs with.
        """
        return cls(
            list_reader=list_reader,
            chat_actor=chat_actor,
            classifier=classifier,
            policy=policy,
            settings=settings,
            log=lambda line: broker.append_log(task_id, line),
            is_cancelled=lambda: is_task_cancelled(broker, task_id),
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    async def scan(self) -> TriageReport:
        """Run one First-Screen Scan and report what it found and did."""
        if not await self._enter_list():
            return TriageReport(
                stop_reason=StopReason.LIST_UNREACHABLE, dry_run=self.settings.dry_run
            )

        await self._log(
            f"📥 [List] 已进入「仅沟通」列表，本次只扫描首屏最多 {VIEWPORT_SIZE} 张卡片，不翻页"
        )

        counters: Counter[TriageKind] = Counter()
        visited_keys: set[str] = set()
        blacklisted: list[str] = []
        evaluated = scanned = rejections = guardrail_blocked = 0
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
                # Nothing on screen is new. Marking a conversation 不感兴趣 removes it
                # from the list, so whatever is still here is what this run cannot
                # advance -- and the scan is finished by construction.
                stop_reason = StopReason.FIRST_SCREEN_EXHAUSTED
                await self._log(
                    f"✅ [首屏] 首屏 {len(visible)} 张卡片均已处理，本次不翻页，扫描结束"
                    f"（stop_reason={stop_reason.value}）"
                )
                break

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
                    f"终止列表扫描"
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
            rehearsed=counters[TriageKind.DRY_RUN],
            rejections=rejections,
            guardrail_blocked=guardrail_blocked,
            blacklisted_companies=tuple(blacklisted),
            visited_keys=frozenset(visited_keys),
        )

    # ------------------------------------------------------------------
    # Scan entry
    # ------------------------------------------------------------------
    async def _enter_list(self) -> bool:
        """Bring the 仅沟通 list to the front, narrating the recovery it needed."""
        if self.list_reader.is_on_list():
            return True
        # A dispatch can land while the app sits on a job detail, an open chat, a
        # filter sheet or even the launcher, so the list is navigated to rather than
        # assumed (issue #228).
        await self._log("🔄 [Navigation] 当前不在「仅沟通」列表，启动自愈导航返回「消息」栏目")
        if self.list_reader.ensure_open_list():
            return True
        await self._log("❌ [List] 无法进入「仅沟通」列表，任务终止")
        return False

    # ------------------------------------------------------------------
    # Per-card stages
    # ------------------------------------------------------------------
    async def _triage_card(self, card: CommunicationCard) -> TriageOutcome:
        """Classify one card and act on it when it is an explicit rejection."""
        sender = card.sender_name or "未知招聘者"

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
            await self._log(
                f"⏭️ [正常消息·LLM判定] '{sender}': {preview} {note}— 保留，不处理"
            )
            return TriageOutcome(kind=TriageKind.PRESERVED)

        await self._log(
            f"🎯 [拒信命中] '{sender}'"
            f"{f' ({card.company_position})' if card.company_position else ''}"
            f": {preview} (置信度 {verdict.confidence:.2f}, "
            f"依据: {verdict.rationale or '未说明'})"
        )

        blacklisted, guardrail_blocked = await self._ingest_blacklist(card, sender)

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

        outcome = await self._acknowledge(card, sender)
        return replace(
            outcome,
            is_rejection=True,
            blacklisted=blacklisted,
            guardrail_blocked=guardrail_blocked,
        )

    async def _ingest_blacklist(self, card: CommunicationCard, sender: str) -> tuple[bool, bool]:
        """Add the card's employer to the company blacklist and persist it.

        Returns (newly_blacklisted, guardrail_blocked). Never let a missing employer
        or a guardrail refusal abort the acknowledgment: the rejection is still worth
        closing politely.
        """
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
            await self._log(
                f"⚠️ [持久化] 企业 '{company}' 已加入内存黑名单，但写入配置失败: {exc}"
            )
            return True, False

        if written is None:
            await self._log(
                f"⚠️ [持久化] 企业 '{company}' 已加入内存黑名单，但 "
                f"{self.policy.source_path or '筛选配置'} 无法安全写入，本次未落盘",
            )
            return True, False

        await self._log(
            f"🚫 [黑名单] 已将企业 '{company}' 加入公司黑名单并写入 {written}"
        )
        return True, False

    async def _acknowledge(self, card: CommunicationCard, sender: str) -> TriageOutcome:
        """Open the chat, send the polite reply, and submit disinterest feedback."""
        if not self.list_reader.open_card(card):
            await self._log(f"❌ [Chat Open Error] 无法打开与 '{sender}' 的会话，已跳过")
            return TriageOutcome(kind=TriageKind.FAILED)

        if not self.chat_actor.send_reply(self.settings.rejection_reply_text):
            await self._log(
                f"❌ [Send Error] 向 '{sender}' 发送礼貌回复失败，回退到列表"
            )
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

        await self._log(
            f"🗑️ [Disinterest] 已将 '{sender}' 标记为不感兴趣（重复推荐）"
        )

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
    "TriageOutcome",
    "TriageReport",
]
