"""
src/boss_agent/worker
=====================
Out-of-process Automation Worker package.
"""

from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.daemon import AutomationWorker
from boss_agent.worker.handlers import (
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
