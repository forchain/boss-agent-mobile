"""
boss_agent.core.automation.use_cases.view_probe
===============================================
Presence checks for a declarative ``ViewDescriptor``, kept off the value object.
"""

from __future__ import annotations

from collections.abc import Callable

from ..domain.locators import ViewDescriptor
from ..interfaces.ui_driver import UIDriverPort
from .wait import wait_until


class ViewPresenceChecker:
    """Checks whether every required selector of a view is on screen."""

    def __init__(
        self,
        driver: UIDriverPort | None,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.driver = driver
        self._clock = clock
        self._sleep = sleep

    def is_present(self, view: ViewDescriptor) -> bool:
        """True when all required selectors for this view exist on the current screen."""
        if not self.driver:
            return False
        for sel in view.required_selectors:
            try:
                elements = self.driver.find_elements(by=sel.by.value, value=sel.value)
                if not elements:
                    return False
            except Exception:
                return False
        return True

    def wait_until_present(self, view: ViewDescriptor, timeout_sec: float = 10.0) -> bool:
        """Wait until all required selectors for this view are present."""
        return bool(
            wait_until(
                lambda: self.is_present(view),
                timeout_sec=timeout_sec,
                error_message=f"Timed out waiting for view '{view.name}' to be present",
                clock=self._clock,
                sleep=self._sleep,
            )
        )
