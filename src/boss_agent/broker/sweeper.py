"""
src/boss_agent/broker/sweeper.py
================================
Background task lease sweeper for crash detection and orphan task recovery.
"""

import asyncio
import logging

from boss_agent.broker.models import TaskStatus
from boss_agent.broker.pocketbase_adapter import BaseTaskBroker

logger = logging.getLogger("boss_agent.sweeper")


class TaskLeaseSweeper:
    """Detects expired running tasks and re-queues or fails them based on retry limits."""

    def __init__(
        self,
        broker: BaseTaskBroker,
        lease_timeout_sec: float = 60.0,
        retry_limit: int = 2,
    ) -> None:
        self.broker = broker
        self.lease_timeout_sec = lease_timeout_sec
        self.retry_limit = retry_limit
        self._running = False

    async def sweep_stale_tasks(self) -> list[str]:
        """Inspect running tasks and recover those with expired heartbeats."""
        stale_tasks = await self.broker.list_stale_running_tasks(
            lease_timeout_sec=self.lease_timeout_sec
        )
        recovered_ids: list[str] = []

        for task in stale_tasks:
            worker_str = task.worker_id or "unknown"
            if task.retry_count < self.retry_limit:
                new_retry = task.retry_count + 1
                await self.broker.append_log(
                    task.id,
                    f"Worker lease expired (Worker: {worker_str}). Re-queuing task for retry (Attempt {new_retry}/{task.max_retries}).",
                )
                await self.broker.requeue_task(task.id, retry_count=new_retry)
                logger.info("Re-queued orphan task %s (Attempt %d)", task.id, new_retry)
            else:
                await self.broker.append_log(
                    task.id,
                    f"Worker lease expired (Worker: {worker_str}). Max retries ({task.max_retries}) exceeded. Worker crash recovery marked task as FAILED.",
                )
                await self.broker.update_task_status(
                    task.id,
                    status=TaskStatus.FAILED,
                    error_message=f"Worker crash / lease expired after {task.retry_count} retries",
                )
                logger.warning("Failed orphan task %s due to exceeded retries", task.id)

            recovered_ids.append(task.id)

        return recovered_ids

    async def start(self, interval_sec: float = 30.0) -> None:
        """Start the periodic lease sweeper loop."""
        self._running = True
        while self._running:
            try:
                await self.sweep_stale_tasks()
            except Exception as e:
                logger.exception("Error during task lease sweeping: %s", e)
            await asyncio.sleep(interval_sec)

    def stop(self) -> None:
        """Stop the sweeper loop."""
        self._running = False
