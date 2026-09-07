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
    PocketBaseBroker,
    PocketBaseTaskBroker,
)
from boss_agent.broker.sweeper import TaskLeaseSweeper

__all__ = [
    "AutomationTask",
    "BaseTaskBroker",
    "InMemoryTaskBroker",
    "POCKETBASE_AUTOMATION_TASKS_SCHEMA",
    "PocketBaseBroker",
    "PocketBaseTaskBroker",
    "TaskLeaseSweeper",
    "TaskStatus",
    "TaskType",
]
