"""
src/boss_agent/worker/daemon.py
===============================
Daemon loop for the out-of-process Automation Worker.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Sequence
from typing import Any, cast

from boss_agent.broker.models import AutomationTask, TaskStatus, TaskType
from boss_agent.broker.pocketbase_adapter import BaseTaskBroker
from boss_agent.startup_cleanup import StartupCleanupGate
from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.handlers.base import BaseTaskHandler, HandlerResult

logger = logging.getLogger("boss_agent.worker")


def _format_payload_for_logging(
    obj: Any,
    max_str_len: int = 120,
    max_list_items: int = 5,
    large_doc_keys: tuple[str, ...] = ("profile_document", "job_description", "raw_html", "resume_text"),
) -> Any:
    """Recursively truncate payload structures for readable, non-bloated console logging."""
    if isinstance(obj, dict):
        truncated: dict[str, Any] = {}
        for k, v in obj.items():
            if str(k) in large_doc_keys and isinstance(v, str) and len(v) > 50:
                truncated[k] = f"<{len(v)} chars: {v[:40].strip()}...>"
            else:
                truncated[k] = _format_payload_for_logging(v, max_str_len, max_list_items, large_doc_keys)
        return truncated

    if isinstance(obj, list):
        if len(obj) > max_list_items:
            items = [
                _format_payload_for_logging(x, max_str_len, max_list_items, large_doc_keys)
                for x in obj[:max_list_items]
            ]
            items.append(f"... (+{len(obj) - max_list_items} more items)")
            return items
        return [
            _format_payload_for_logging(x, max_str_len, max_list_items, large_doc_keys)
            for x in obj
        ]

    if isinstance(obj, str):
        if len(obj) > max_str_len:
            return f"{obj[:max_str_len]}... (total {len(obj)} chars)"
        return obj

    return obj


class _TaskLoggingBrokerProxy:
    """Non-mutating proxy for BaseTaskBroker that mirrors handler progress logs to worker console."""

    def __init__(self, target: BaseTaskBroker) -> None:
        self._target = target

    async def append_log(self, task_id: str, log_line: str) -> bool:
        logger.info("📝 [Task %s] %s", task_id, log_line)
        return await self._target.append_log(task_id, log_line)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)


class AutomationWorker:
    """Out-of-process task execution daemon bound 1:1 to a device session."""

    def __init__(
        self,
        config: WorkerConfig,
        broker: BaseTaskBroker,
        context: WorkerContext | None = None,
        handlers: Sequence[BaseTaskHandler] | None = None,
        driver: Any | None = None,
        startup_gate: StartupCleanupGate | None = None,
    ) -> None:
        self.config = config
        self.broker = broker
        self.context = context or WorkerContext(config=config, driver=driver)
        self.startup_gate = startup_gate or StartupCleanupGate(
            broker, enabled=config.run_cleanup_on_startup
        )
        self._handlers: dict[TaskType, BaseTaskHandler] = {}
        if handlers:
            for h in handlers:
                self.register_handler(h)

        self._running = False

    def register_handler(self, handler: BaseTaskHandler) -> None:
        """Register a polymorphic task handler."""
        self._handlers[handler.task_type] = handler

    async def _heartbeat_loop(self, task_id: str) -> None:
        """Background task renewing the heartbeat lease periodically."""
        while True:
            try:
                await asyncio.sleep(self.config.heartbeat_interval_sec)
                await self.broker.update_heartbeat(task_id, worker_id=self.config.worker_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Heartbeat update failed for task %s: %s", task_id, e)

    async def _finalize_task_outcome(
        self,
        task: AutomationTask,
        task_type_str: str,
        start_time: float,
        result: HandlerResult,
    ) -> None:
        """Evaluate task outcome, log completion status and update state in broker."""
        duration = time.monotonic() - start_time
        cur = await self.broker.get_task(task.id)
        if cur and cur.status == TaskStatus.CANCELLED:
            logger.info(
                "⚠️ Task %s was cancelled during execution (%.2fs); preserving CANCELLED status",
                task.id,
                duration,
            )
        elif result.success:
            logger.info(
                "✅ Task %s [%s] completed successfully in %.2fs",
                task.id,
                task_type_str,
                duration,
            )
            await self.broker.update_task_status(
                task.id,
                status=TaskStatus.SUCCESS,
            )
        else:
            logger.error(
                "❌ Task %s [%s] failed in %.2fs: %s",
                task.id,
                task_type_str,
                duration,
                result.error_message,
            )
            await self.broker.update_task_status(
                task.id,
                status=TaskStatus.FAILED,
                error_message=result.error_message,
            )

    async def run_once(self) -> bool:
        """Attempt to claim and process one pending task.

        The startup 拒信清扫 is claimed ahead of anything queued before it, and every
        search task is held back until it settles (#230).

        The scan window is the gate's: a cleanup queued at startup sits behind any
        task left pending by an earlier run, so a narrower window could leave the
        cleanup unseen while its own barrier held every task in view.
        """
        pending_tasks = await self.broker.list_pending_tasks(
            limit=self.startup_gate.queue_scan_limit
        )
        if not pending_tasks:
            return False

        claimed_task: AutomationTask | None = None
        held = await self.startup_gate.held_types()
        for task in self.startup_gate.prioritize(pending_tasks):
            if task.task_type in held:
                logger.info(
                    "⏸️ Task %s [%s] held back until the startup 拒信清扫 settles",
                    task.id,
                    task.task_type.value,
                )
                continue
            claimed = await self.broker.claim_task(task.id, worker_id=self.config.worker_id)
            if claimed:
                claimed_task = claimed
                break

        if not claimed_task:
            return False

        task_type_str = claimed_task.task_type.value
        preview_payload = _format_payload_for_logging(claimed_task.payload or {})
        logger.info(
            "📥 Claimed task %s [type=%s] for device %s (payload=%s)",
            claimed_task.id,
            task_type_str,
            self.config.device_id,
            preview_payload,
        )
        start_time = time.monotonic()

        handler = self._handlers.get(claimed_task.task_type)
        if not handler:
            logger.error(
                "❌ No handler registered for task type %s (task_id=%s)",
                task_type_str,
                claimed_task.id,
            )
            await self.broker.append_log(
                claimed_task.id, f"No handler registered for task type {claimed_task.task_type}"
            )
            await self.broker.update_task_status(
                claimed_task.id,
                status=TaskStatus.FAILED,
                error_message=f"Unsupported task type: {claimed_task.task_type}",
            )
            return True

        # Non-mutating broker proxy that mirrors handler append_log calls to worker console
        handler_broker = cast(BaseTaskBroker, _TaskLoggingBrokerProxy(self.broker))

        # Start heartbeat background renewal
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(claimed_task.id))
        try:
            result = await handler.handle(claimed_task, handler_broker, self.context)
            await self._finalize_task_outcome(claimed_task, task_type_str, start_time, result)
        except Exception as e:
            duration = time.monotonic() - start_time
            cur = await self.broker.get_task(claimed_task.id)
            if cur and cur.status == TaskStatus.CANCELLED:
                logger.info(
                    "⚠️ Task %s was cancelled, ignoring exception: %s (%.2fs)",
                    claimed_task.id,
                    e,
                    duration,
                )
            else:
                logger.exception(
                    "❌ Task %s [%s] execution raised uncaught exception in %.2fs: %s",
                    claimed_task.id,
                    task_type_str,
                    duration,
                    e,
                )
                await self.broker.append_log(claimed_task.id, f"Uncaught exception: {e}")
                await self.broker.update_task_status(
                    claimed_task.id,
                    status=TaskStatus.FAILED,
                    error_message=str(e),
                )
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task

        return True

    async def start(self, max_runs: int | None = None) -> None:
        """Start the worker execution polling loop."""
        self._running = True
        logger.info(
            "🚀 Worker daemon loop started for %s bound to device %s (poll_interval=%.1fs)",
            self.config.worker_id,
            self.config.device_id,
            self.config.poll_interval_sec,
        )
        # Queued before the loop so the first claim of this startup is the cleanup
        # rather than a search task that could re-apply to a rejecting employer.
        await self.startup_gate.arm()
        runs = 0
        while self._running:
            try:
                did_work = await self.run_once()
                if did_work:
                    runs += 1
                    if max_runs is not None and runs >= max_runs:
                        break
                else:
                    await asyncio.sleep(self.config.poll_interval_sec)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in worker execution loop: %s", e)
                await asyncio.sleep(self.config.poll_interval_sec)
        logger.info("🛑 Worker daemon loop stopped for %s", self.config.worker_id)

    def stop(self) -> None:
        """Signal the worker loop to stop."""
        self._running = False
