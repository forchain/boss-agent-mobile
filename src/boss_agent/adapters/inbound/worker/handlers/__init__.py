"""
src/boss_agent/worker/handlers
==============================
Polymorphic task handlers for automation workflows.
"""

from boss_agent.adapters.inbound.worker.handlers.auto_apply import AutoApplyHandler
from boss_agent.adapters.inbound.worker.handlers.base import BaseTaskHandler, HandlerResult
from boss_agent.adapters.inbound.worker.handlers.check_chat import CheckChatHandler
from boss_agent.adapters.inbound.worker.handlers.check_login import CheckLoginHandler
from boss_agent.adapters.inbound.worker.handlers.scrape_jobs import ScrapeJobsHandler

__all__ = [
    "AutoApplyHandler",
    "BaseTaskHandler",
    "CheckChatHandler",
    "CheckLoginHandler",
    "HandlerResult",
    "ScrapeJobsHandler",
]
