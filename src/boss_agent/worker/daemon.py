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
from typing import Any

from boss_agent.broker.models import AutomationTask, TaskStatus, TaskType
from boss_agent.broker.pocketbase_adapter import BaseTaskBroker
from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.handlers.base import BaseTaskHandler

logger = logging.getLogger("boss_agent.worker")


class AutomationWorker:
    """Out-of-process task execution daemon bound 1:1 to a device session."""

    def __init__(
        self,
        config: WorkerConfig,
        broker: BaseTaskBroker,
        context: WorkerContext | None = None,
        handlers: Sequence[BaseTaskHandler] | None = None,
        driver: Any | None = None,
    ) -> None:
        self.config = config
        self.broker = broker
        self.context = context or WorkerContext(config=config, driver=driver)
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

    async def run_once(self) -> bool:
        """Attempt to claim and process one pending task."""
        pending_tasks = await self.broker.list_pending_tasks(limit=5)
        if not pending_tasks:
            return False

        claimed_task: AutomationTask | None = None
        for task in pending_tasks:
            claimed = await self.broker.claim_task(task.id, worker_id=self.config.worker_id)
            if claimed:
                claimed_task = claimed
                break

        if not claimed_task:
            return False

        task_type_str = (
            claimed_task.task_type.value
            if hasattr(claimed_task.task_type, "value")
            else str(claimed_task.task_type)
        )
        logger.info(
            "📥 Claimed task %s [type=%s] for device %s (payload=%s)",
            claimed_task.id,
            task_type_str,
            self.config.device_id,
            claimed_task.payload or {},
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

        # Intercept broker.append_log to mirror handler progress logs to console
        original_append_log = self.broker.append_log

        async def logging_append_log(tid: str, line: str) -> bool:
            logger.info("📝 [Task %s] %s", tid, line)
            return await original_append_log(tid, line)

        self.broker.append_log = logging_append_log  # type: ignore[assignment]

        # Start heartbeat background renewal
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(claimed_task.id))
        try:
            result = await handler.handle(claimed_task, self.broker, self.context)
            duration = time.monotonic() - start_time
            cur = await self.broker.get_task(claimed_task.id)
            if cur and cur.status == TaskStatus.CANCELLED:
                logger.info(
                    "⚠️ Task %s was cancelled during execution (%.2fs); preserving CANCELLED status",
                    claimed_task.id,
                    duration,
                )
            elif result.success:
                logger.info(
                    "✅ Task %s [%s] completed successfully in %.2fs",
                    claimed_task.id,
                    task_type_str,
                    duration,
                )
                await self.broker.update_task_status(
                    claimed_task.id,
                    status=TaskStatus.SUCCESS,
                )
            else:
                logger.error(
                    "❌ Task %s [%s] failed in %.2fs: %s",
                    claimed_task.id,
                    task_type_str,
                    duration,
                    result.error_message,
                )
                await self.broker.update_task_status(
                    claimed_task.id,
                    status=TaskStatus.FAILED,
                    error_message=result.error_message,
                )
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
            self.broker.append_log = original_append_log  # type: ignore[assignment]
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
