"""Task cancellation probe over the TaskBroker port."""

from __future__ import annotations

from boss_agent.core.task.domain.models import TaskStatus
from boss_agent.core.task.interfaces.broker import BaseTaskBroker


async def is_task_cancelled(broker: BaseTaskBroker, task_id: str) -> bool:
    """Whether the user cancelled the task this run belongs to."""
    task = await broker.get_task(task_id)
    return task is not None and task.status == TaskStatus.CANCELLED


__all__ = ["is_task_cancelled"]
