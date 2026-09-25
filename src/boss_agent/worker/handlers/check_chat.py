"""
src/boss_agent/worker/handlers/check_chat.py
============================================
Handler for CHECK_CHAT: triages the 仅沟通 communication list, blacklists the
employer behind every explicit recruiter rejection, and politely closes the
conversation (Issues #205-#208).

The list is scanned from the cards alone. Cards carrying the Outbound Message
Indicator are bypassed without any LLM call, so a thread waiting on a recruiter
reply costs nothing.

The scan reads the *opening screen* and nothing else (ADR 0015). The list is
ordered newest-first, and marking a conversation 不感兴趣 drops it from the
list, so the top of the screen is both where new state arrives and where the
list drains from. A pass therefore judges what it can see, re-reads the screen,
and stops once nothing on it is new: no scrolling, no pagination, and no
per-run accumulation to reason about.

Paging was tried twice and removed. The list yields an unbounded run of
zero-token outbound cards and of preserved cards that are never removed, so
neither a depth bound nor a card ceiling nor a time cursor could keep a run
from walking the whole backlog on every dispatch (Issues #239, ADR 0014).

The cost is coverage, and it is deliberate: a card below the fold is reached
only after the cards above it leave the list. See ADR 0015.

The two page objects a run drives arrive through the injectable ``pages`` seam
rather than being constructed inline, so a test scripts the device world instead
of patching this module's globals (spec #267).
"""

import logging
from collections import Counter
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from boss_agent.broker.models import AutomationTask, TaskStatus, TaskType
from boss_agent.broker.pocketbase_adapter import BaseTaskBroker
from boss_agent.models import ScreeningPolicy
from boss_agent.pages import (
    OUTBOUND_STATUS_MARKERS,
    ChatPage,
    CommunicationCard,
    CommunicationListPage,
    StartupDialogPage,
)
from boss_agent.rejection import ChatAcknowledgmentSettings, RejectionClassifier
from boss_agent.settings import resolve_chat_acknowledgment_settings
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.handlers.base import BaseTaskHandler, HandlerResult

logger = logging.getLogger(__name__)

#: Cards read from the opening screen per pass. Also the scan's width: the handler
#: never scrolls, so this bounds how much of the list one run can reach.
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

#: `stop_reason` recorded when every card on the opening screen has been handled.
FIRST_SCREEN_EXHAUSTED = "first_screen_exhausted"


class TriageKind(StrEnum):
    """What the handler did with one communication card."""

    #: Outbound Message Indicator present: waiting on the recruiter, never touched.
    SKIPPED = "skipped"
    PRESERVED = "preserved"
    ACKNOWLEDGED = "acknowledged"
    DRY_RUN = "dry_run"
    FAILED = "failed"


@dataclass(frozen=True)
class TriageOutcome:
    """Result of triaging one card, for per-run task telemetry."""

    kind: TriageKind
    is_rejection: bool = False
    #: Set when the employer was newly added to the company blacklist.
    blacklisted: bool = False
    #: Set when a guardrail (headhunter agency / masked name) refused the employer.
    guardrail_blocked: bool = False
    navigated: bool = False
    #: Set when the platform never returned to the list; the scan must stop
    #: rather than read cards off an unknown screen.
    lost_list: bool = False


@dataclass(frozen=True)
class CheckChatPages:
    """The device collaborators one CHECK_CHAT run drives.

    A run's device world is composed from its driver and handed in, so a test can
    script the 仅沟通 screen and the chat without reaching into this module.
    """

    list_page: Any
    chat_page: Any

    @classmethod
    def for_driver(cls, driver: Any) -> "CheckChatPages":
        """Compose the production device world for ``driver``.

        The startup dialog is dismissed here, before any page object is handed to a
        run: a dispatch can land on it, and a dialog left up would swallow the
        clicks the run is about to make.
        """
        startup_page = StartupDialogPage(driver)
        if startup_page.is_dialog_present():
            startup_page.dismiss_dialog()
        return cls(list_page=CommunicationListPage(driver), chat_page=ChatPage(driver))


class CheckChatHandler(BaseTaskHandler):
    """Executes 仅沟通 rejection triage with blacklist ingestion."""

    def __init__(
        self,
        llm_client: Any | None = None,
        classifier: Any | None = None,
        settings: ChatAcknowledgmentSettings | None = None,
        policy: ScreeningPolicy | None = None,
        pages: Any | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.classifier = classifier or RejectionClassifier(llm_client=llm_client)
        # Resolved lazily so constructing a handler never reads local config.
        self._settings = settings
        self._policy = policy
        # A factory over the driver, because the driver only exists at dispatch time.
        self._pages = pages or CheckChatPages.for_driver

    @property
    def task_type(self) -> TaskType:
        return TaskType.CHECK_CHAT

    def _resolve_settings(self, payload: dict[str, Any]) -> ChatAcknowledgmentSettings:
        if self._settings is None:
            self._settings = resolve_chat_acknowledgment_settings()
        return self._settings.with_overrides(payload)

    def _resolve_policy(self) -> ScreeningPolicy:
        """Resolve the screening policy the blacklist is ingested into.

        The injected policy wins (tests and any caller that already holds one);
        otherwise the active local configuration is loaded, and remembers the file
        it came from so the blacklist is written back to the same place.
        """
        if self._policy is not None:
            return self._policy
        return ScreeningPolicy.load_default()

    async def handle(
        self,
        task: AutomationTask,
        broker: BaseTaskBroker,
        context: WorkerContext,
    ) -> HandlerResult:
        driver = context.driver
        if not driver:
            await broker.append_log(task.id, "Error: No driver session initialized")
            return HandlerResult(success=False, error_message="Driver session is unavailable")

        payload = task.payload or {}
        settings = self._resolve_settings(payload)
        dry_run = settings.dry_run
        reply_text = settings.rejection_reply_text
        max_scan_depth = settings.max_scan_depth
        policy = self._resolve_policy()

        await broker.append_log(
            task.id,
            f"Starting CHECK_CHAT (dry_run={dry_run}, max_scan_depth={max_scan_depth}, "
            f"reply='{reply_text}')",
        )

        pages = self._pages(driver)
        comm_list = pages.list_page
        chat_page = pages.chat_page

        if not comm_list.is_on_list(timeout_sec=1.0):
            # The dispatch can land while the app sits on a job detail, an open chat,
            # a filter sheet or even the launcher, so the list is navigated to rather
            # than assumed (issue #228).
            await broker.append_log(
                task.id, "🔄 [Navigation] 当前不在「仅沟通」列表，启动自愈导航返回「消息」栏目"
            )
            if not comm_list.open_list(timeout_sec=PAGE_TIMEOUT_SEC):
                await broker.append_log(task.id, "❌ [List] 无法进入「仅沟通」列表，任务终止")
                return HandlerResult(success=False, error_message="Failed to open 仅沟通 list")

        await broker.append_log(
            task.id,
            f"📥 [List] 已进入「仅沟通」列表，本次只扫描首屏最多 {VIEWPORT_SIZE} 张卡片，不翻页",
        )

        counters: Counter[str] = Counter()
        detected_rejections = 0
        evaluated = 0
        scanned = 0
        blacklisted: list[str] = []
        guardrail_blocked = 0
        visited_keys: set[str] = set()
        stop_reason = "completed"

        while True:
            cur_task = await broker.get_task(task.id)
            if cur_task and cur_task.status == TaskStatus.CANCELLED:
                stop_reason = "cancelled"
                await broker.append_log(task.id, "🛑 [Task Cancelled] 任务已被取消，终止列表扫描")
                break

            visible = comm_list.extract_visible_messages(max_items=VIEWPORT_SIZE)
            if not visible:
                stop_reason = "empty_list"
                break

            pending = [message for message in visible if message.key not in visited_keys]
            if not pending:
                # Nothing on screen is new. Marking a conversation 不感兴趣 removes it
                # from the list, so whatever is still here is what this run cannot
                # advance -- and the scan is finished by construction.
                stop_reason = FIRST_SCREEN_EXHAUSTED
                await broker.append_log(
                    task.id,
                    f"✅ [首屏] 首屏 {len(visible)} 张卡片均已处理，本次不翻页，扫描结束"
                    f"（stop_reason={FIRST_SCREEN_EXHAUSTED}）",
                )
                break

            lost_list = False
            for message in pending:
                if evaluated >= max_scan_depth or scanned >= MAX_INSPECTED_CARDS:
                    break
                visited_keys.add(message.key)
                scanned += 1
                outcome = await self._triage_message(
                    task,
                    broker,
                    comm_list,
                    chat_page,
                    policy,
                    message,
                    dry_run=dry_run,
                    reply_text=reply_text,
                )
                counters[outcome.kind] += 1
                if outcome.kind is not TriageKind.SKIPPED:
                    evaluated += 1
                    detected_rejections += int(outcome.is_rejection)
                if outcome.blacklisted and message.company_name:
                    blacklisted.append(message.company_name)
                guardrail_blocked += int(outcome.guardrail_blocked)
                if outcome.navigated:
                    # The platform shifted the remaining cards up: abandon this
                    # screen snapshot and re-read before continuing.
                    lost_list = outcome.lost_list
                    break

            if lost_list:
                # We are no longer looking at the list; reading cards here could
                # interact with an unrelated screen, so stop instead.
                stop_reason = "lost_list"
                break
            if evaluated >= max_scan_depth:
                stop_reason = "max_scan_depth"
                break
            if scanned >= MAX_INSPECTED_CARDS:
                stop_reason = "scan_ceiling"
                await broker.append_log(
                    task.id,
                    f"🛑 [Scan Ceiling] 单次扫描已达 {MAX_INSPECTED_CARDS} 张卡片上限，终止列表扫描",
                )
                break
            # Loop back for another read of the same screen: an acknowledged card leaves
            # the list, and the cards under it move up into the space it freed.
            # `visited_keys` is what keeps a preserved card from being judged twice.

        blacklist_note = f" [{', '.join(blacklisted)}]" if blacklisted else ""
        summary = (
            f"Finished CHECK_CHAT: scanned {scanned} card(s), "
            f"evaluated {evaluated} message(s), "
            f"{counters[TriageKind.SKIPPED]} skipped as outbound, "
            f"{detected_rejections} rejection(s) detected, "
            f"{len(blacklisted)} company(ies) blacklisted{blacklist_note}, "
            f"{guardrail_blocked} blocked by guardrails, "
            f"{counters[TriageKind.ACKNOWLEDGED]} acknowledged, "
            f"{counters[TriageKind.PRESERVED]} preserved, "
            f"{counters[TriageKind.FAILED]} failed "
            f"(stop_reason={stop_reason}, dry_run={dry_run})"
        )
        await broker.append_log(task.id, summary)
        return HandlerResult(
            success=True,
            output={
                "dry_run": dry_run,
                "reply_text": reply_text,
                "max_scan_depth": max_scan_depth,
                "scanned": scanned,
                "evaluated": evaluated,
                "skipped_outbound": counters[TriageKind.SKIPPED],
                "rejections": detected_rejections,
                "blacklisted": len(blacklisted),
                "blacklisted_companies": blacklisted,
                "guardrail_blocked": guardrail_blocked,
                "acknowledged": counters[TriageKind.ACKNOWLEDGED],
                "preserved": counters[TriageKind.PRESERVED],
                "failed": counters[TriageKind.FAILED],
                "stop_reason": stop_reason,
                "visited_keys": sorted(visited_keys),
            },
        )

    async def _triage_message(
        self,
        task: AutomationTask,
        broker: BaseTaskBroker,
        comm_list: CommunicationListPage,
        chat_page: ChatPage,
        policy: ScreeningPolicy,
        message: CommunicationCard,
        *,
        dry_run: bool,
        reply_text: str,
    ) -> TriageOutcome:
        """Classify one card and act on it when it is an explicit rejection."""
        sender = message.sender_name or "未知招聘者"

        if message.has_outbound_indicator:
            desc = "存在未发送草稿" if "草稿" in message.outbound_status else "我发出后对方未回复"
            await broker.append_log(
                task.id,
                f"⏭️ [出站等待·零Token] '{sender}': {desc}"
                f"（{message.outbound_status}），跳过，零 Token 消耗",
            )
            return TriageOutcome(kind=TriageKind.SKIPPED)

        if message.outbound_status.strip():
            # An unrecognised badge is evaluated rather than skipped, so a wording
            # change on the platform can never silently disable rejection triage.
            await broker.append_log(
                task.id,
                f"⚠️ [未知状态标签] '{sender}': '{message.outbound_status}' 不匹配已知出站标记"
                f"（{'/'.join(OUTBOUND_STATUS_MARKERS)}），按入站消息继续判定",
            )

        verdict = self.classifier.classify(message.message_text, message.sender_name)
        preview = _preview(message.message_text)

        if not verdict.is_rejection:
            note = f"（判定异常: {verdict.error}）" if verdict.error else ""
            await broker.append_log(
                task.id,
                f"⏭️ [正常消息·LLM判定] '{sender}': {preview} {note}— 保留，不处理",
            )
            return TriageOutcome(kind=TriageKind.PRESERVED)

        await broker.append_log(
            task.id,
            f"🎯 [拒信命中] '{sender}'"
            f"{f' ({message.company_position})' if message.company_position else ''}"
            f": {preview} (置信度 {verdict.confidence:.2f}, "
            f"依据: {verdict.rationale or '未说明'})",
        )

        blacklisted, guardrail_blocked = await self._ingest_blacklist(
            task, broker, policy, message, sender, dry_run=dry_run
        )

        if dry_run:
            await broker.append_log(
                task.id,
                f"🧪 [DRY-RUN] 拟回复 '{reply_text}' 并将该会话标记为不感兴趣（重复推荐），"
                "本次演练不执行任何实际操作",
            )
            return TriageOutcome(
                kind=TriageKind.DRY_RUN,
                is_rejection=True,
                blacklisted=blacklisted,
                guardrail_blocked=guardrail_blocked,
            )

        outcome = await self._acknowledge_rejection(
            task, broker, comm_list, chat_page, message, sender, reply_text
        )
        return replace(
            outcome,
            is_rejection=True,
            blacklisted=blacklisted,
            guardrail_blocked=guardrail_blocked,
        )

    async def _ingest_blacklist(
        self,
        task: AutomationTask,
        broker: BaseTaskBroker,
        policy: ScreeningPolicy,
        message: CommunicationCard,
        sender: str,
        *,
        dry_run: bool,
    ) -> tuple[bool, bool]:
        """Add the card's employer to the company blacklist and persist it.

        Returns (newly_blacklisted, guardrail_blocked). Never let a missing
        employer or a guardrail refusal abort the acknowledgment: the rejection
        is still worth closing politely.
        """
        company = message.company_name
        if not company:
            await broker.append_log(
                task.id,
                f"⚠️ [企业解析] 无法从 '{sender}' 的卡片解析出企业名"
                f"（{message.company_position or '无 [公司] | [岗位] 描述'}），跳过拉黑",
            )
            return False, False

        already_listed = company in policy.company_blacklist

        if dry_run:
            allowed, notice = policy.validate_can_blacklist_company(company)
            if allowed:
                await broker.append_log(
                    task.id,
                    f"🧪 [DRY-RUN] 拟将企业 '{company}' 加入公司黑名单并写入筛选配置，"
                    "本次演练不修改任何文件",
                )
                return False, False
            await broker.append_log(task.id, f"🧪 [DRY-RUN] {notice}")
            return False, True

        added, notice = policy.add_company_to_blacklist(company)
        if not added:
            await broker.append_log(task.id, f"🛡️ {notice}")
            return False, True

        if already_listed:
            await broker.append_log(task.id, f"ℹ️ 企业 '{company}' 已在公司黑名单中，无需重复写入")
            return False, False

        try:
            written = policy.persist_company_blacklist(company)
        except Exception as exc:  # noqa: BLE001 - the in-memory policy still holds
            await broker.append_log(
                task.id,
                f"⚠️ [持久化] 企业 '{company}' 已加入内存黑名单，但写入配置失败: {exc}",
            )
            return True, False

        if written is None:
            await broker.append_log(
                task.id,
                f"⚠️ [持久化] 企业 '{company}' 已加入内存黑名单，但 {policy.source_path or '筛选配置'}"
                " 无法安全写入，本次未落盘",
            )
            return True, False

        await broker.append_log(
            task.id, f"🚫 [黑名单] 已将企业 '{company}' 加入公司黑名单并写入 {written}"
        )
        return True, False

    async def _acknowledge_rejection(
        self,
        task: AutomationTask,
        broker: BaseTaskBroker,
        comm_list: CommunicationListPage,
        chat_page: ChatPage,
        message: CommunicationCard,
        sender: str,
        reply_text: str,
    ) -> TriageOutcome:
        """Open the chat, send the polite reply, and submit disinterest feedback."""
        if not comm_list.open_message(message):
            await broker.append_log(
                task.id, f"❌ [Chat Open Error] 无法打开与 '{sender}' 的会话，已跳过"
            )
            return TriageOutcome(kind=TriageKind.FAILED)

        if not chat_page.send_message(reply_text, timeout_sec=PAGE_TIMEOUT_SEC):
            await broker.append_log(
                task.id, f"❌ [Send Error] 向 '{sender}' 发送礼貌回复失败，回退到列表"
            )
            chat_page.navigate_back(timeout_sec=PAGE_TIMEOUT_SEC)
            return TriageOutcome(kind=TriageKind.FAILED, navigated=True)

        await broker.append_log(task.id, f"✅ [Reply Sent] 已向 '{sender}' 发送 '{reply_text}'")

        if not comm_list.mark_disinterest(timeout_sec=PAGE_TIMEOUT_SEC):
            await broker.append_log(
                task.id,
                "❌ [不感兴趣 Error] “不感兴趣”入口或“重复推荐”选项未出现，"
                "已放弃本次标记并回退到列表",
            )
            chat_page.navigate_back(timeout_sec=PAGE_TIMEOUT_SEC)
            return TriageOutcome(kind=TriageKind.FAILED, navigated=True)

        await broker.append_log(
            task.id, f"🗑️ [Disinterest] 已将 '{sender}' 标记为不感兴趣（重复推荐）"
        )

        if not comm_list.wait_for_list_return(timeout_sec=PAGE_TIMEOUT_SEC):
            await broker.append_log(
                task.id,
                "⚠️ [Navigation] 标记后未能确认返回列表；为避免在未知页面上误操作，终止本次扫描",
            )
            return TriageOutcome(kind=TriageKind.ACKNOWLEDGED, navigated=True, lost_list=True)

        return TriageOutcome(kind=TriageKind.ACKNOWLEDGED, navigated=True)


def _preview(text: str) -> str:
    flat = " ".join((text or "").split())
    if len(flat) <= PREVIEW_CHARS:
        return flat
    return flat[:PREVIEW_CHARS] + "…"


__all__ = ["CheckChatHandler", "TriageOutcome"]
