"""
boss_agent.adapters.outbound.device.appium.session
==================================================
Appium/WebDriver session lifecycle behind ``DeviceSessionPort``.
"""

from __future__ import annotations

import contextlib

from appium import webdriver

from boss_agent.core.automation.domain.errors import DeviceSessionError
from boss_agent.core.automation.interfaces.ui_driver import UIDriverPort

from .config import DriverConfig
from .driver_adapter import AppiumDriverAdapter


class AppiumSession:
    """Manages an active Appium WebDriver session."""

    def __init__(self, config: DriverConfig) -> None:
        self.config = config
        self.driver: webdriver.Remote | None = None
        self.adapter: AppiumDriverAdapter | None = None

    @property
    def raw_driver(self) -> webdriver.Remote | None:
        return self.driver

    def start(self) -> UIDriverPort:
        """Initialize and connect to the Appium server."""
        options = self.config.to_options()
        executor = self.config.server_url
        if "://0.0.0.0" in executor:
            executor = executor.replace("://0.0.0.0", "://127.0.0.1")
        try:
            self.driver = webdriver.Remote(command_executor=executor, options=options)
        except Exception as e:
            raise DeviceSessionError(f"Could not start Appium session at {executor}: {e}") from e
        if self.config.app_package and hasattr(self.driver, "activate_app"):
            with contextlib.suppress(Exception):
                self.driver.activate_app(self.config.app_package)
        self.adapter = AppiumDriverAdapter(self.driver)
        return self.adapter

    def activate_app(self, app_id: str | None = None) -> bool:
        """Activate the specified application or the configured app_package."""
        target = app_id or self.config.app_package
        if target and self.driver and hasattr(self.driver, "activate_app"):
            try:
                self.driver.activate_app(target)
                return True
            except Exception:
                pass
        return False

    def stop(self) -> None:
        """Gracefully terminate the WebDriver session."""
        if self.driver:
            with contextlib.suppress(Exception):
                self.driver.quit()
            self.driver = None
            self.adapter = None

    def is_active(self) -> bool:
        return self.driver is not None
