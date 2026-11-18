"""
src/boss_agent/broker
=====================
State Stream Task Broker package facade.

Public symbols resolve lazily (PEP 562). The concrete broker and its stores live in
``boss_agent.adapters``; this package keeps the symbol names callers have always
imported and re-points them at their new homes, so a caller does not have to know
which adapter currently provides the task stream.

The lazy form is still load-bearing: the task models, the broker port, and the two
broker implementations sit in different layers, and an eager import closes an import
cycle through ``boss_agent.adapters.outbound.pocketbase``. These names resolve to the
identical objects they did before the re-homing.
"""

from __future__ import annotations

import importlib

_LAZY_EXPORTS: dict[str, str] = {
    "AutomationTask": "boss_agent.core.task.domain.models",
    "BaseTaskBroker": "boss_agent.core.task.interfaces.broker",
    "InMemoryTaskBroker": "boss_agent.adapters.memory.task_broker",
    "PocketBaseBroker": "boss_agent.adapters.outbound.pocketbase.pocketbase_adapter",
    "PocketBaseTaskBroker": "boss_agent.adapters.outbound.pocketbase.pocketbase_adapter",
    "TaskLeaseSweeper": "boss_agent.core.task.use_cases.sweeper",
    "TaskStatus": "boss_agent.core.task.domain.models",
    "TaskType": "boss_agent.core.task.domain.models",
}


def __getattr__(name: str) -> object:
    """Resolve a public symbol on first access, then cache it (PEP 562)."""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        value = getattr(importlib.import_module(module_name, __name__), name)
    except ImportError as exc:
        raise AttributeError(
            f"module {__name__!r} cannot provide {name!r}: "
            f"importing {module_name!r} failed with {exc!r}"
        ) from exc
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})


__all__ = [
    "AutomationTask",
    "BaseTaskBroker",
    "InMemoryTaskBroker",
    "PocketBaseBroker",
    "PocketBaseTaskBroker",
    "TaskLeaseSweeper",
    "TaskStatus",
    "TaskType",
]
