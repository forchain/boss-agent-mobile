"""
src/boss_agent/adapters/inbound/worker/handlers/base.py
=======================================================
Base abstract class for polymorphic task handlers.
"""

from abc import ABC, abstractmethod
from typing import NamedTuple

from boss_agent.adapters.inbound.worker.context import WorkerContext
from boss_agent.core.task.domain.models import AutomationTask, TaskType
from boss_agent.core.task.interfaces.broker import BaseTaskBroker


class HandlerResult(NamedTuple):
    """Result of task handler execution."""

    success: bool
    error_message: str | None = None
    output: dict | None = None


class BaseTaskHandler(ABC):
    """Abstract interface for task type handlers."""

    @property
    @abstractmethod
    def task_type(self) -> TaskType:
        """The specific TaskType this handler executes."""
        pass

    @abstractmethod
    async def handle(
        self,
        task: AutomationTask,
        broker: BaseTaskBroker,
        context: WorkerContext,
    ) -> HandlerResult:
        """Execute the automation workflow for the given task."""
        pass
