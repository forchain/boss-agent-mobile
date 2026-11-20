"""
boss_agent.adapters.outbound.device.appium.interceptors
======================================================
Android system permission dialogs, dismissed through ``ScreenInterceptorPort``.
"""

from __future__ import annotations

from boss_agent.core.automation.domain.locators import UISelector, parse_selector
from boss_agent.core.automation.interfaces.locator_store import LocatorStorePort
from boss_agent.core.automation.interfaces.ui_driver import UIDriverPort


class AndroidSystemDialogInterceptor:
    """Auto-dismisses Android system permission prompts and privacy alerts."""

    def __init__(self, locator_store: LocatorStorePort) -> None:
        self.locators = locator_store
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

    def can_handle(self, driver: UIDriverPort) -> bool:
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

    def handle(self, driver: UIDriverPort) -> bool:
        for sel in self.permission_selectors:
            try:
                elements = driver.find_elements(by=sel.by.value, value=sel.value)
                if elements:
                    elements[0].click()
                    return True
            except Exception:
                continue
        return False


#: Historical name.
SystemDialogInterceptor = AndroidSystemDialogInterceptor
