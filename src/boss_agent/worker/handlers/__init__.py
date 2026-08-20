"""
src/boss_agent/worker/handlers
==============================
Polymorphic task handlers for automation workflows.
"""

from boss_agent.worker.handlers.auto_apply import AutoApplyHandler
from boss_agent.worker.handlers.base import BaseTaskHandler, HandlerResult
from boss_agent.worker.handlers.check_login import CheckLoginHandler
from boss_agent.worker.handlers.scrape_jobs import ScrapeJobsHandler

__all__ = [
    "AutoApplyHandler",
    "BaseTaskHandler",
    "CheckLoginHandler",
    "HandlerResult",
    "ScrapeJobsHandler",
]
