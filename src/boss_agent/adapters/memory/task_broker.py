"""
boss_agent.adapters.memory.task_broker
======================================
Thread-safe in-memory Task Broker for tests and local development.

Composes the three in-memory repository stores, exactly as the PocketBase adapter
composes its own — so the fast unit tier drives the same broker/store seam the
production adapter exposes.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from boss_agent.adapters.memory.candidate_memory_store import InMemoryCandidateMemoryStore
from boss_agent.adapters.memory.job_store import InMemoryJobRecordStore
from boss_agent.adapters.memory.saved_search_store import InMemorySavedSearchStore
from boss_agent.core.task.domain.models import AutomationTask, TaskStatus, TaskType
from boss_agent.core.task.interfaces.broker import BaseTaskBroker

logger = logging.getLogger("boss_agent.adapters.memory.task_broker")

__all__ = ["InMemoryTaskBroker"]


class InMemoryTaskBroker(BaseTaskBroker):
    """Thread-safe & asyncio-safe in-memory broker for tests and local development."""

    def __init__(self) -> None:
        self._tasks: dict[str, AutomationTask] = {}
        self.job_store = InMemoryJobRecordStore()
        self.candidate_memory = InMemoryCandidateMemoryStore()
        self.saved_searches = InMemorySavedSearchStore()
        self._lock = asyncio.Lock()
        self._subscribers: list[Callable[[str, AutomationTask], Any]] = []

    async def create_task(
        self,
        task_type: TaskType | str,
        payload: dict[str, Any] | None = None,
        source: str = "manual",
    ) -> AutomationTask:
        resolved_type = task_type if isinstance(task_type, TaskType) else TaskType(task_type)
        now = datetime.now(UTC)
        task = AutomationTask(
            task_type=resolved_type,
            status=TaskStatus.PENDING,
            payload=payload or {},
            source=source,
            worker_id=None,
            locked_at=None,
            last_heartbeat_at=None,
            logs=[],
            error_message=None,
            created=now,
            updated=now,
        )
        async with self._lock:
            self._tasks[task.id] = task

        await self._notify_subscribers("create", task)
        return copy.deepcopy(task)

    async def claim_task(self, task_id: str, worker_id: str) -> AutomationTask | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            if task.status != TaskStatus.PENDING:
                return None

            now = datetime.now(UTC)
            task.status = TaskStatus.RUNNING
            task.worker_id = worker_id
            task.locked_at = now
            task.last_heartbeat_at = now
            task.updated = now
            claimed_task = copy.deepcopy(task)

        await self._notify_subscribers("update", claimed_task)
        return claimed_task

    async def get_task(self, task_id: str) -> AutomationTask | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            return copy.deepcopy(task) if task else None

    async def update_heartbeat(self, task_id: str, worker_id: str) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            if task.worker_id != worker_id or task.status != TaskStatus.RUNNING:
                return False

            now = datetime.now(UTC)
            task.last_heartbeat_at = now
            task.updated = now
            return True

    async def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        logs: list[str] | None = None,
        error_message: str | None = None,
        worker_id: str | None = None,
    ) -> AutomationTask:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(f"Task {task_id} not found")

            now = datetime.now(UTC)
            task.status = status
            if logs is not None:
                task.logs = list(logs)
            if error_message is not None:
                task.error_message = error_message
            if worker_id is not None:
                task.worker_id = worker_id
            task.updated = now
            updated_task = copy.deepcopy(task)

        await self._notify_subscribers("update", updated_task)
        return updated_task

    async def append_log(self, task_id: str, log_line: str) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            now = datetime.now(UTC)
            formatted = f"[{now.isoformat()}] {log_line}"
            task.logs.append(formatted)
            task.updated = now
            return True

    async def list_pending_tasks(self, limit: int = 10) -> list[AutomationTask]:
        async with self._lock:
            pending = [
                copy.deepcopy(t) for t in self._tasks.values() if t.status == TaskStatus.PENDING
            ]
            pending.sort(key=lambda x: x.created)
            return pending[:limit]

    async def list_stale_running_tasks(
        self, lease_timeout_sec: float = 60.0
    ) -> list[AutomationTask]:
        async with self._lock:
            now = datetime.now(UTC)
            stale: list[AutomationTask] = []
            for t in self._tasks.values():
                if t.status == TaskStatus.RUNNING:
                    hb = t.last_heartbeat_at or t.locked_at or t.created
                    # Normalize timezone
                    if hb.tzinfo is None:
                        hb = hb.replace(tzinfo=UTC)
                    if (now - hb).total_seconds() > lease_timeout_sec:
                        stale.append(copy.deepcopy(t))
            return stale

    async def requeue_task(self, task_id: str, retry_count: int) -> AutomationTask:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(f"Task {task_id} not found")
            now = datetime.now(UTC)
            task.status = TaskStatus.PENDING
            task.worker_id = None
            task.locked_at = None
            task.last_heartbeat_at = None
            task.retry_count = retry_count
            task.updated = now
            requeued = copy.deepcopy(task)

        await self._notify_subscribers("update", requeued)
        return requeued

    def subscribe_tasks(self, callback: Callable[[str, AutomationTask], Any]) -> None:
        self._subscribers.append(callback)

    async def _notify_subscribers(self, event_type: str, task: AutomationTask) -> None:
        for sub in self._subscribers:
            try:
                res = sub(event_type, copy.deepcopy(task))
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.exception("Error in task subscription callback: %s", e)
