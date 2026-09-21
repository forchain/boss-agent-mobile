"""
src/boss_agent/worker/handlers/check_chat.py
============================================
Handler for CHECK_CHAT: triages the New Greeting Inbox (新招呼), politely
acknowledges explicit recruiter rejections, and submits standardized
disinterest feedback so the conversation is pruned and future recommendations
are suppressed (Issues #205-#207).
"""

import logging
from collections import Counter
from dataclasses import dataclass
from typing import Any

from boss_agent.broker.models import AutomationTask, TaskStatus, TaskType
from boss_agent.broker.pocketbase_adapter import BaseTaskBroker
from boss_agent.pages import ChatInboxMessage, ChatInboxPage, ChatPage, StartupDialogPage
from boss_agent.rejection import ChatAcknowledgmentSettings, RejectionClassifier
from boss_agent.settings import resolve_chat_acknowledgment_settings
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.handlers.base import BaseTaskHandler, HandlerResult

logger = logging.getLogger(__name__)

#: Triage outcome codes, counted per run for structured task telemetry.
PRESERVED = "preserved"
ACKNOWLEDGED = "acknowledged"
DRY_RUN = "dry_run"
FAILED = "failed"

#: Consecutive scrolls yielding no new inbox content before the traversal gives up.
MAX_STALLED_SCROLLS = 3

#: Inbox cards read per viewport pass.
VIEWPORT_SIZE = 10

#: Characters of message text echoed into task logs.
PREVIEW_CHARS = 40

#: Timeout budget for a single inbox/dialog interaction.
PAGE_TIMEOUT_SEC = 5.0


@dataclass(frozen=True)
class TriageOutcome:
    """What the handler did with one inbox message."""

    kind: str
    navigated: bool = False


class CheckChatHandler(BaseTaskHandler):
    """Executes New Greeting Inbox triage with rejection auto-acknowledgment."""

    def __init__(
        self,
        llm_client: Any | None = None,
        classifier: Any | None = None,
        settings: ChatAcknowledgmentSettings | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.classifier = classifier or RejectionClassifier(llm_client=llm_client)
        # Resolved lazily so constructing a handler never reads local config.
        self._settings = settings

    @property
    def task_type(self) -> TaskType:
        return TaskType.CHECK_CHAT

    def _resolve_settings(self, payload: dict[str, Any]) -> ChatAcknowledgmentSettings:
        if self._settings is None:
            self._settings = resolve_chat_acknowledgment_settings()
        return self._settings.with_overrides(payload)

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
        dry_run = bool(payload.get("dry_run", False))
        settings = self._resolve_settings(payload)
        reply_text = settings.rejection_reply_text
        max_scan_depth = settings.max_scan_depth

        await broker.append_log(
            task.id,
            f"Starting CHECK_CHAT (dry_run={dry_run}, max_scan_depth={max_scan_depth}, "
            f"reply='{reply_text}')",
        )

        startup_page = StartupDialogPage(driver)
        if startup_page.is_dialog_present():
            startup_page.dismiss_dialog()

        inbox = ChatInboxPage(driver)
        chat_page = ChatPage(driver)

        if not inbox.is_on_inbox(timeout_sec=1.0) and not inbox.open_inbox(
            timeout_sec=PAGE_TIMEOUT_SEC
        ):
            await broker.append_log(task.id, "❌ [Inbox] 无法进入新招呼收件箱，任务终止")
            return HandlerResult(success=False, error_message="Failed to open New Greeting Inbox")

        await broker.append_log(task.id, "📥 [Inbox] 已进入新招呼收件箱，开始扫描未回复消息")

        counters: Counter[str] = Counter()
        visited_keys: set[str] = set()
        scanned = 0
        stalled_scrolls = 0
        stop_reason = "completed"

        while scanned < max_scan_depth:
            cur_task = await broker.get_task(task.id)
            if cur_task and cur_task.status == TaskStatus.CANCELLED:
                stop_reason = "cancelled"
                await broker.append_log(
                    task.id, "🛑 [Task Cancelled] 任务已被取消，终止收件箱扫描"
                )
                break

            visible = inbox.extract_visible_messages(max_items=VIEWPORT_SIZE)
            if not visible:
                stop_reason = "empty_inbox"
                break

            pending = [m for m in visible if m.key not in visited_keys]
            if not pending:
                # Every visible card is already handled; there may be older ones above.
                stalled_scrolls += 1
                if stalled_scrolls > MAX_STALLED_SCROLLS:
                    stop_reason = "stalled"
                    await broker.append_log(
                        task.id,
                        f"🛑 [Feed Safeguard] 连续 {MAX_STALLED_SCROLLS} 次翻页无新消息，终止收件箱扫描",
                    )
                    break
                await broker.append_log(
                    task.id, "↕️ [Pagination] 当前可见消息均已处理，向上滑动加载更早的消息"
                )
                inbox.scroll_inbox()
                continue

            stalled_scrolls = 0
            navigated = False
            for message in pending:
                if scanned >= max_scan_depth:
                    break
                visited_keys.add(message.key)
                scanned += 1
                outcome = await self._triage_message(
                    task,
                    broker,
                    inbox,
                    chat_page,
                    message,
                    dry_run=dry_run,
                    reply_text=reply_text,
                )
                counters[outcome.kind] += 1
                if outcome.navigated:
                    # The platform shifted the remaining cards up: abandon this
                    # viewport snapshot and re-read before continuing.
                    navigated = True
                    break

            if scanned >= max_scan_depth:
                stop_reason = "max_scan_depth"
                break
            if navigated:
                continue
            inbox.scroll_inbox()

        rejections = counters[DRY_RUN] + counters[ACKNOWLEDGED] + counters[FAILED]
        summary = (
            f"Finished CHECK_CHAT: scanned {scanned} message(s), "
            f"{rejections} rejection(s) detected, {counters[ACKNOWLEDGED]} acknowledged, "
            f"{counters[PRESERVED]} preserved, {counters[FAILED]} failed "
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
                "rejections": rejections,
                "acknowledged": counters[ACKNOWLEDGED],
                "preserved": counters[PRESERVED],
                "failed": counters[FAILED],
                "stop_reason": stop_reason,
                "visited_keys": sorted(visited_keys),
            },
        )

    async def _triage_message(
        self,
        task: AutomationTask,
        broker: BaseTaskBroker,
        inbox: ChatInboxPage,
        chat_page: ChatPage,
        message: ChatInboxMessage,
        *,
        dry_run: bool,
        reply_text: str,
    ) -> TriageOutcome:
        """Classify one inbox message and act on it when it is an explicit rejection."""
        verdict = self.classifier.classify(message.message_text, message.sender_name)
        sender = message.sender_name or "未知招聘者"
        preview = _preview(message.message_text)

        if not verdict.is_rejection:
            note = f"（判定异常: {verdict.error}）" if verdict.error else ""
            await broker.append_log(
                task.id,
                f"⏭️ [正常消息] '{sender}': {preview} {note}— 保留，不处理",
            )
            return TriageOutcome(kind=PRESERVED)

        await broker.append_log(
            task.id,
            f"🎯 [拒信命中] '{sender}': {preview} "
            f"(置信度 {verdict.confidence:.2f}, 依据: {verdict.rationale or '未说明'})",
        )

        if dry_run:
            await broker.append_log(
                task.id,
                f"🧪 [DRY-RUN] 拟回复 '{reply_text}' 并将该会话标记为不感兴趣（重复推荐），"
                "本次演练不执行任何实际操作",
            )
            return TriageOutcome(kind=DRY_RUN)

        return await self._acknowledge_rejection(
            task, broker, inbox, chat_page, message, sender, reply_text
        )

    async def _acknowledge_rejection(
        self,
        task: AutomationTask,
        broker: BaseTaskBroker,
        inbox: ChatInboxPage,
        chat_page: ChatPage,
        message: ChatInboxMessage,
        sender: str,
        reply_text: str,
    ) -> TriageOutcome:
        """Open the chat, send the polite reply, and submit disinterest feedback."""
        if not inbox.open_message(message):
            await broker.append_log(
                task.id, f"❌ [Chat Open Error] 无法打开与 '{sender}' 的会话，已跳过"
            )
            return TriageOutcome(kind=FAILED)

        if not chat_page.send_message(reply_text, timeout_sec=PAGE_TIMEOUT_SEC):
            await broker.append_log(
                task.id, f"❌ [Send Error] 向 '{sender}' 发送礼貌回复失败，回退到收件箱"
            )
            chat_page.navigate_back(timeout_sec=PAGE_TIMEOUT_SEC)
            return TriageOutcome(kind=FAILED, navigated=True)

        await broker.append_log(task.id, f"✅ [Reply Sent] 已向 '{sender}' 发送 '{reply_text}'")

        if not inbox.mark_disinterest(timeout_sec=PAGE_TIMEOUT_SEC):
            await broker.append_log(
                task.id,
                "❌ [不感兴趣 Error] “不感兴趣”入口或“重复推荐”选项未出现，"
                "已放弃本次标记并回退到收件箱",
            )
            chat_page.navigate_back(timeout_sec=PAGE_TIMEOUT_SEC)
            return TriageOutcome(kind=FAILED, navigated=True)

        await broker.append_log(
            task.id, f"🗑️ [Disinterest] 已将 '{sender}' 标记为不感兴趣（重复推荐）"
        )

        if not inbox.wait_for_inbox_return(timeout_sec=PAGE_TIMEOUT_SEC):
            await broker.append_log(
                task.id, "⚠️ [Navigation] 标记后未能确认返回收件箱列表，继续扫描"
            )

        return TriageOutcome(kind=ACKNOWLEDGED, navigated=True)


def _preview(text: str) -> str:
    flat = " ".join((text or "").split())
    if len(flat) <= PREVIEW_CHARS:
        return flat
    return flat[:PREVIEW_CHARS] + "…"


__all__ = ["CheckChatHandler", "TriageOutcome"]
