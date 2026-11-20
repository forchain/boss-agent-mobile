"""
boss_agent.core.automation.domain.errors
========================================
Typed failures for the mobile UI automation aggregate.
"""

from __future__ import annotations


class AutomationError(RuntimeError):
    """Base exception for mobile automation failures."""


class DeviceSessionError(AutomationError):
    """A device session could not be started, activated, or probed."""


class ElementWaitTimeoutError(AutomationError, TimeoutError):
    """A bounded wait expired.

    Also a builtin ``TimeoutError`` so callers that already catch that keep working.
    """


class AdbCommandError(AutomationError):
    """An ADB command could not be executed."""
