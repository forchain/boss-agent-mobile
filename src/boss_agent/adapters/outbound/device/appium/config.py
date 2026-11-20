"""
boss_agent.adapters.outbound.device.appium.config
=================================================
Appium Android capability configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
