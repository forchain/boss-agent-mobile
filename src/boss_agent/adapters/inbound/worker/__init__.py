"""
src/boss_agent/adapters/inbound/worker
======================================
Out-of-process Automation Worker package.
"""

from boss_agent.adapters.inbound.worker.config import WorkerConfig
from boss_agent.adapters.inbound.worker.context import WorkerContext
from boss_agent.adapters.inbound.worker.daemon import AutomationWorker
from boss_agent.adapters.inbound.worker.handlers import (
    AutoApplyHandler,
    BaseTaskHandler,
    CheckLoginHandler,
    HandlerResult,
    ScrapeJobsHandler,
)

__all__ = [
    "AutoApplyHandler",
    "AutomationWorker",
    "BaseTaskHandler",
    "CheckLoginHandler",
    "HandlerResult",
    "ScrapeJobsHandler",
    "WorkerConfig",
    "WorkerContext",
]
