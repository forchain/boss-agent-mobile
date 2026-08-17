"""
droid_agent_core.driver
=======================
Appium & ADB session lifecycle management.
"""

import subprocess
from dataclasses import dataclass, field
from typing import Any

from appium import webdriver
from appium.options.android import UiAutomator2Options


@dataclass
class DriverConfig:
    server_url: str = "http://127.0.0.1:4723"
    platform_name: str = "Android"
    automation_name: str = "UiAutomator2"
    device_name: str = "Android Emulator"
    app_package: str | None = None
    app_activity: str | None = None
    no_reset: bool = True
    auto_grant_permissions: bool = True
    new_command_timeout: int = 300
    extra_capabilities: dict[str, Any] = field(default_factory=dict)

    def to_options(self) -> UiAutomator2Options:
        options = UiAutomator2Options()
        options.platform_name = self.platform_name
        options.automation_name = self.automation_name
        options.device_name = self.device_name
        if self.app_package:
            options.app_package = self.app_package
        if self.app_activity:
            options.app_activity = self.app_activity
        options.no_reset = self.no_reset
        options.auto_grant_permissions = self.auto_grant_permissions
        options.new_command_timeout = self.new_command_timeout
        for k, v in self.extra_capabilities.items():
            options.set_capability(k, v)
        return options


class AppiumSession:
    """Manages an active Appium WebDriver session."""

    def __init__(self, config: DriverConfig):
        self.config = config
        self.driver: webdriver.Remote | None = None

    def start(self) -> webdriver.Remote:
        """Initialize and connect to the Appium server."""
        options = self.config.to_options()
        self.driver = webdriver.Remote(command_executor=self.config.server_url, options=options)
        return self.driver

    def stop(self) -> None:
        """Gracefully terminate the WebDriver session."""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            finally:
                self.driver = None

    def is_active(self) -> bool:
        return self.driver is not None


class AdbClient:
    """Helper for executing direct ADB commands on target device."""

    def __init__(self, device_id: str | None = None):
        self.device_id = device_id

    def run(self, args: list[str]) -> subprocess.CompletedProcess:
        cmd = ["adb"]
        if self.device_id:
            cmd.extend(["-s", self.device_id])
        cmd.extend(args)
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    def get_current_focus(self) -> str:
        res = self.run(["shell", "dumpsys", "window", "|", "grep", "-E", "'mCurrentFocus'"])
        return res.stdout.strip()
