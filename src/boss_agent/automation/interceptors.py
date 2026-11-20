"""
boss_agent.automation.interceptors
==================================
Global dialog, popup, and system permission interceptors.

The interceptor logic is platform-neutral; the selectors it looks for are supplied by
whoever knows the device, so this module never embeds a platform package name or reads
configuration from disk.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from .locators import UISelector
from .port import DriverPort


class BaseInterceptor(ABC):
    """Abstract interceptor for handling unexpected screens, dialogs, or popups."""

    @abstractmethod
    def can_handle(self, driver: DriverPort | None) -> bool:
        """Return True if this interceptor recognizes the current screen/dialog."""

    @abstractmethod
    def handle(self, driver: DriverPort | None) -> bool:
        """Perform action to dismiss or process the dialog. Return True if handled."""


class SystemDialogInterceptor(BaseInterceptor):
    """Auto-dismisses system permission prompts and privacy alerts."""

    def __init__(self, permission_selectors: Sequence[UISelector]):
        self.permission_selectors: list[UISelector] = list(permission_selectors)

    def can_handle(self, driver: DriverPort | None) -> bool:
        if not driver:
            return False
        for sel in self.permission_selectors:
            try:
                elements = driver.find_elements(by=sel.by.value, value=sel.value)
                if elements:
                    return True
            except Exception:
                continue
        return False

    def handle(self, driver: DriverPort | None) -> bool:
        if not driver:
            return False
        for sel in self.permission_selectors:
            try:
                elements = driver.find_elements(by=sel.by.value, value=sel.value)
                if elements:
                    elements[0].click()
                    return True
            except Exception:
                continue
        return False


class InterceptorRegistry:
    """Registry maintaining prioritized interceptors."""

    def __init__(self):
        self.interceptors: list[BaseInterceptor] = []

    def register(self, interceptor: BaseInterceptor) -> None:
        self.interceptors.append(interceptor)

    def process_all(self, driver: DriverPort | None) -> bool:
        """Scan and trigger any matching interceptor."""
        for interceptor in self.interceptors:
            if interceptor.can_handle(driver):
                return interceptor.handle(driver)
        return False
