"""
boss_agent.core.automation.interfaces.interceptor
=================================================
The interceptor port for unexpected screens, dialogs, and popups.
"""

from __future__ import annotations

from typing import Protocol

from .ui_driver import UIDriverPort


class ScreenInterceptorPort(Protocol):
    """An interceptor that recognizes and dismisses one kind of screen."""

    def can_handle(self, driver: UIDriverPort) -> bool: ...

    def handle(self, driver: UIDriverPort) -> bool: ...
