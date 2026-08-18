"""
droid_agent_core.interceptors
=============================
Global dialog, popup, and system permission interceptors.
"""

from abc import ABC, abstractmethod
from typing import Any

from .locators import LocatorRegistry, UISelector, get_global_locator_registry, parse_selector


class BaseInterceptor(ABC):
    """Abstract interceptor for handling unexpected screens, dialogs, or popups."""

    @abstractmethod
    def can_handle(self, driver: Any) -> bool:
        """Return True if this interceptor recognizes the current screen/dialog."""

    @abstractmethod
    def handle(self, driver: Any) -> bool:
        """Perform action to dismiss or process the dialog. Return True if handled."""


class SystemDialogInterceptor(BaseInterceptor):
    """Auto-dismisses Android system permission prompts and privacy alerts."""

    def __init__(self, locator_registry: LocatorRegistry | None = None):
        self.locators = locator_registry or get_global_locator_registry()
        configured = self.locators.get_selectors("system_dialog.permission_buttons")
        self.permission_selectors: list[UISelector] = configured or [
            parse_selector("com.android.permissioncontroller:id/permission_allow_button"),
            parse_selector(
                "com.android.permissioncontroller:id/permission_allow_foreground_only_button"
            ),
            parse_selector("//*[@text='允许' or @text='同意' or @text='确定' or @text='好的']"),
            parse_selector(
                "//*[@text='SKIP' or @text='Skip' or @text='跳过' or @text='CANCEL' or @text='Cancel' or @text='取消' or @text='稍后']"
            ),
        ]

    def can_handle(self, driver: Any) -> bool:
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

    def handle(self, driver: Any) -> bool:
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

    def process_all(self, driver: Any) -> bool:
        """Scan and trigger any matching interceptor."""
        for interceptor in self.interceptors:
            if interceptor.can_handle(driver):
                return interceptor.handle(driver)
        return False
