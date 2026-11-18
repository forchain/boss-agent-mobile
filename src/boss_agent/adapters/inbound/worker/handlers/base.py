"""
src/boss_agent/worker/handlers/base.py
======================================
Base abstract class for polymorphic task handlers.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import NamedTuple

from boss_agent.adapters.inbound.worker.context import WorkerContext
from boss_agent.core.task.domain.models import AutomationTask, TaskType
from boss_agent.core.task.interfaces.broker import BaseTaskBroker


def load_prompt_or_default(loader: Callable[[], str], default: str = "") -> str:
    """Load a prompt document, degrading a missing or unreadable one to ``default``.

    #453 made both documents eager at task start; a missing seed must not fail the whole
    task. Before that, the screening prompt fell back to the screener's built-in rules and
    the greeting document was never loaded on the save-only paths (#461).
    """
    try:
        return loader()
    except OSError:
        return default


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
