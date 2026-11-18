"""
src/boss_agent/core/task/interfaces/broker.py
=============================================
The State Stream Task Broker port (ADR 0013; moved from the PocketBase adapter module).

Confined to task-stream lifecycle and lease heartbeats. Job records, candidate memory
and saved searches are separate seams composed by concrete adapters: depending on this
port must not drag a storage adapter's HTTP stack into the import graph.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from boss_agent.core.task.domain.models import AutomationTask, TaskStatus, TaskType


class TaskBroker(ABC):
    """Abstract interface for the State Stream Task Broker.

    Confined to task-stream lifecycle and lease heartbeats. Job records, exclusion
    pools, cool-down math and quota counting live on the dedicated ``JobRecordStore``
    seam (ADR 0013 §3); the broker does not impersonate it.
    """

    @abstractmethod
    async def create_task(
        self,
        task_type: TaskType | str,
        payload: dict[str, Any] | None = None,
        source: str = "manual",
    ) -> AutomationTask:
        """Create and persist a new task with PENDING status.

        ``source`` is Task Provenance: where the task came from. The worker's startup
        sweep cancels ``test``-sourced tasks so an automated suite never contends with
        the live worker for the device.
        """
        pass

    @abstractmethod
    async def claim_task(self, task_id: str, worker_id: str) -> AutomationTask | None:
        """Atomically claim a pending task for a worker. Returns None if already claimed."""
        pass

    @abstractmethod
    async def get_task(self, task_id: str) -> AutomationTask | None:
        """Fetch a task by ID."""
        pass

    @abstractmethod
    async def update_heartbeat(self, task_id: str, worker_id: str) -> bool:
        """Update last_heartbeat_at timestamp for a running task."""
        pass

    @abstractmethod
    async def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        logs: list[str] | None = None,
        error_message: str | None = None,
        worker_id: str | None = None,
    ) -> AutomationTask:
        """Transition task to a new status and optionally update logs / error message."""
        pass

    @abstractmethod
    async def append_log(self, task_id: str, log_line: str) -> bool:
        """Append a single log line to the task."""
        pass

    async def flush_logs(self, task_id: str | None = None) -> None:  # noqa: B027
        """Flush any buffered log lines to the persistent broker."""
        pass

    @abstractmethod
    async def list_pending_tasks(self, limit: int = 10) -> list[AutomationTask]:
        """List unassigned tasks currently in PENDING state."""
        pass

    @abstractmethod
    async def list_stale_running_tasks(
        self, lease_timeout_sec: float = 60.0
    ) -> list[AutomationTask]:
        """List running tasks whose heartbeat has expired."""
        pass

    @abstractmethod
    async def requeue_task(self, task_id: str, retry_count: int) -> AutomationTask:
        """Re-queue an expired task back to PENDING state."""
        pass

    @abstractmethod
    def subscribe_tasks(self, callback: Callable[[str, AutomationTask], Any]) -> None:
        """Register a callback for task lifecycle events (create, update)."""
        pass


#: Compatibility name still advertised by the ``boss_agent.broker`` facade.
BaseTaskBroker = TaskBroker

__all__ = ["BaseTaskBroker", "TaskBroker"]
