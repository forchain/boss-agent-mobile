"""
src/boss_agent/broker
=====================
State Stream Broker package for PocketBase integration and asynchronous worker coordination.
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

__all__ = [
    "POCKETBASE_AUTOMATION_TASKS_SCHEMA",
    "AutomationTask",
    "BaseTaskBroker",
    "InMemoryTaskBroker",
    "PocketBaseTaskBroker",
    "TaskStatus",
    "TaskType",
]
