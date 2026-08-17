"""
tests/e2e/test_live_device_smoke.py
===================================
Real hardware live automated smoke test on connected Android device/emulator.
Run with: pytest -m live tests/e2e/test_live_device_smoke.py
"""

import os
import time

import pytest

from boss_agent.pages import LoginPage, StartupDialogPage
from droid_agent_core.driver import AppiumSession, DriverConfig


@pytest.mark.live
def test_live_app_launch_and_startup_interaction():
    """Verify live Appium session against real Boss 直聘 App."""
    server_url = os.environ.get("APPIUM_SERVER_URL", "http://127.0.0.1:4723")
    udid = os.environ.get("ANDROID_UDID", "emulator-5554")

    config = DriverConfig(
        server_url=server_url,
        platform_name="Android",
        automation_name="UiAutomator2",
        device_name=udid,
        app_package="com.hpbr.bosszhipin",
        app_activity="com.hpbr.bosszhipin.module.launcher.WelcomeActivity",
        no_reset=True,
        auto_grant_permissions=True,
        new_command_timeout=120,
        extra_capabilities={
            "appium:udid": udid,
            "appium:uiautomator2ServerInstallTimeout": 60000,
            "appium:adbExecTimeout": 60000,
        },
    )

    session = AppiumSession(config)
    try:
        driver = session.start()
        assert driver is not None

        # Verify page source is accessible
        source = driver.page_source
        assert len(source) > 0

        # Handle startup agreement if present
        startup = StartupDialogPage(driver)
        if startup.is_dialog_present():
            startup.dismiss_dialog()
            time.sleep(1.0)

        # Check authentication page detection
        login_page = LoginPage(driver)
        status = login_page.get_auth_status()
        assert status is not None
    finally:
        session.stop()
