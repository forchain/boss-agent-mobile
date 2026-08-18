"""
droid_agent_core.interceptors
=============================
Global dialog, popup, and system permission interceptors.
"""

from abc import ABC, abstractmethod
from typing import Any

from .locators import By, UISelector


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

    def __init__(self):
        self.permission_selectors = [
            UISelector(
                By.ID, "com.android.permissioncontroller:id/permission_allow_button", "Allow button"
            ),
            UISelector(
                By.ID,
                "com.android.permissioncontroller:id/permission_allow_foreground_only_button",
                "While using app",
            ),
            UISelector(
                By.XPATH,
                "//*[@text='允许' or @text='同意' or @text='确定' or @text='好的']",
                "Allow/Agree Chinese button",
            ),
            UISelector(
                By.XPATH,
                "//*[@text='SKIP' or @text='Skip' or @text='跳过' or @text='CANCEL' or @text='Cancel' or @text='取消' or @text='稍后']",
                "Skip/Cancel button",
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
