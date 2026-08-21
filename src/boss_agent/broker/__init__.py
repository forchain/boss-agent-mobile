"""
src/boss_agent/broker
=====================
State Stream Task Broker package.
"""

from boss_agent.broker.models import (
    POCKETBASE_AUTOMATION_TASKS_SCHEMA,
    AutomationTask,
    TaskStatus,
    TaskType,
)
from boss_agent.broker.pocketbase_adapter import (
    BaseTaskBroker,
    InMemoryTaskBroker,
    PocketBaseTaskBroker,
)
from boss_agent.broker.sweeper import TaskLeaseSweeper

__all__ = [
    "AutomationTask",
    "BaseTaskBroker",
    "InMemoryTaskBroker",
    "POCKETBASE_AUTOMATION_TASKS_SCHEMA",
    "PocketBaseTaskBroker",
    "TaskLeaseSweeper",
    "TaskStatus",
    "TaskType",
]
