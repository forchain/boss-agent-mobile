"""
src/boss_agent/worker/daemon.py
===============================
Daemon loop for the out-of-process Automation Worker.
"""

import asyncio
import contextlib
import logging
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

        handler = self._handlers.get(claimed_task.task_type)
        if not handler:
            await self.broker.append_log(
                claimed_task.id, f"No handler registered for task type {claimed_task.task_type}"
            )
            await self.broker.update_task_status(
                claimed_task.id,
                status=TaskStatus.FAILED,
                error_message=f"Unsupported task type: {claimed_task.task_type}",
            )
            return True

        # Start heartbeat background renewal
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(claimed_task.id))
        try:
            result = await handler.handle(claimed_task, self.broker, self.context)
            if result.success:
                await self.broker.update_task_status(
                    claimed_task.id,
                    status=TaskStatus.SUCCESS,
                )
            else:
                await self.broker.update_task_status(
                    claimed_task.id,
                    status=TaskStatus.FAILED,
                    error_message=result.error_message,
                )
        except Exception as e:
            logger.exception("Task %s execution raised uncaught exception: %s", claimed_task.id, e)
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
        runs = 0
        while self._running:
            did_work = await self.run_once()
            if did_work:
                runs += 1
                if max_runs is not None and runs >= max_runs:
                    break
            else:
                await asyncio.sleep(self.config.poll_interval_sec)

    def stop(self) -> None:
        """Signal the worker loop to stop."""
        self._running = False
