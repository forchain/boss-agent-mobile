"""
src/boss_agent/broker
=====================
State Stream Task Broker package.

Public symbols resolve lazily (PEP 562).

``pocketbase_adapter`` imports ``boss_agent.job_store`` for the shared
``JobRecordStore`` seam, and ``job_store`` imports
``boss_agent.adapters.outbound.pocketbase.collection_schema`` for the wire schema. Those two edges form a
cycle, and this ``__init__`` closed it by importing both ends eagerly: whichever side
was reached first lost, and the loser's names silently vanished.

That was hidden because ``boss_agent/__init__.py`` wrapped its own ``job_store``
import in ``contextlib.suppress(ImportError)``. Before Issue #386, seven names in
``boss_agent.__all__`` (``JobRecordStore``, ``InMemoryJobRecordStore``,
``PocketBaseJobRecordStore``, and the four ``feed_pipeline`` symbols that import from
them) were advertised but could not actually be resolved -- a suppressed circular
import, not a missing feature.

Deferring the adapter breaks the cycle at its actual source: ``job_store`` can now
reach ``boss_agent.adapters.outbound.pocketbase.collection_schema`` (a leaf with no first-party imports)
without dragging ``pocketbase_adapter`` back into a half-initialized state. The names
below resolve to the identical objects they did before.
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
