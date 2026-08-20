"""
src/boss_agent/worker/handlers
==============================
Polymorphic task handlers for automation workflows.
"""

from boss_agent.worker.handlers.base import BaseTaskHandler, HandlerResult
from boss_agent.worker.handlers.check_login import CheckLoginHandler

__all__ = [
    "BaseTaskHandler",
    "CheckLoginHandler",
    "HandlerResult",
]
