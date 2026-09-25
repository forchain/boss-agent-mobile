"""
src/boss_agent/worker/handlers/check_chat.py
============================================
Handler for CHECK_CHAT: dispatches one 仅沟通 rejection-triage run and reports it.

The scan, the stop-reason taxonomy, the Outbound Message Indicator bypass, the
classify → guardrail → blacklist-ingest → acknowledge ordering and the per-run
tallies all live in the deep ``ChatTriage`` module (spec #267); this handler only
resolves a run's settings and screening policy from the task payload, dispatches the
run, and maps the Triage Report into the task telemetry the Task Management
Dashboard renders. It touches no page object, no card and no device.

The device world a run drives is composed by the Chat Triage module from the
driver, through the ``pages`` factory this handler hands in — overridable, so a test
scripts the screen and the chat instead of patching this module's globals (#268).
"""

import logging
from typing import Any

from boss_agent.broker.models import AutomationTask, TaskType
from boss_agent.broker.pocketbase_adapter import BaseTaskBroker
from boss_agent.chat_triage import ChatTriage, StopReason, TriagePages
from boss_agent.models import ScreeningPolicy
from boss_agent.rejection import ChatAcknowledgmentSettings, RejectionClassifier
from boss_agent.settings import resolve_chat_acknowledgment_settings
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.handlers.base import BaseTaskHandler, HandlerResult

logger = logging.getLogger(__name__)


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
        self._pages = pages or TriagePages.for_driver

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

        settings = self._resolve_settings(task.payload or {})
        await broker.append_log(
            task.id,
            f"Starting CHECK_CHAT (dry_run={settings.dry_run}, "
            f"max_scan_depth={settings.max_scan_depth}, reply='{settings.rejection_reply_text}')",
        )

        report = await ChatTriage.for_task(
            broker,
            task.id,
            driver,
            classifier=self.classifier,
            policy=self._resolve_policy(),
            settings=settings,
            pages=self._pages,
        ).scan()

        if report.stop_reason is StopReason.LIST_UNREACHABLE:
            return HandlerResult(success=False, error_message="Failed to open 仅沟通 list")

        await broker.append_log(task.id, report.summary_line())
        return HandlerResult(
            success=True,
            output={
                "dry_run": report.dry_run,
                "reply_text": settings.rejection_reply_text,
                "max_scan_depth": settings.max_scan_depth,
                "scanned": report.scanned,
                "evaluated": report.evaluated,
                "skipped_outbound": report.skipped_outbound,
                "rejections": report.rejections,
                "blacklisted": report.blacklisted_count,
                "blacklisted_companies": list(report.blacklisted_companies),
                "guardrail_blocked": report.guardrail_blocked,
                "acknowledged": report.acknowledged,
                "preserved": report.preserved,
                "failed": report.failed,
                "stop_reason": report.stop_reason.value,
                "visited_keys": sorted(report.visited_keys),
            },
        )


__all__ = ["CheckChatHandler"]
