"""
tests/e2e/test_live_search_entry_timing.py
=========================================
Live integration test for the search entry pipeline (spec #193, tickets #195/#197).

Reproduces the path that used to take 148s: stand on a job detail subpage, then
enter search. Asserts the entry completes inside a generous wall-clock bound and
that the pipeline is actually usable end-to-end (results load).

Read-only: it never greets, messages, or applies to anything.

Run with: pytest -m live tests/e2e/test_live_search_entry_timing.py
"""

import os
import time

import pytest

from boss_agent.pages import JobListPage, SearchPage, StartupDialogPage
from droid_agent_core.driver import AppiumSession, DriverConfig

# The 148s regression this pipeline replaced; anything near it means the cascading
# selector scans are back. Generous enough to survive a cold accessibility tree.
ENTRY_BUDGET_SEC = 30.0


@pytest.mark.live
def test_live_search_entry_from_subpage_within_budget():
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

        startup = StartupDialogPage(driver)
        if startup.is_dialog_present():
            startup.dismiss_dialog()

        list_page = JobListPage(driver)
        search_page = SearchPage(driver)

        assert list_page.navigate_to_home(), "could not reach the home anchor"
        assert list_page.select_first_job(timeout_sec=10.0), "no job card to open"

        started_at = time.monotonic()
        entered = list_page.open_search(timeout_sec=10.0)
        elapsed = time.monotonic() - started_at

        assert entered, "search entry failed from the job detail subpage"
        assert search_page.is_search_page(), "search input is not on screen after entry"
        assert elapsed < ENTRY_BUDGET_SEC, (
            f"search entry from a subpage took {elapsed:.1f}s "
            f"(budget {ENTRY_BUDGET_SEC:.0f}s) — check for re-introduced selector cascades"
        )

        assert search_page.search("agent", timeout_sec=15.0), "keyword search did not submit"
        assert list_page.wait_for_jobs_loaded(timeout_sec=15.0), "search results never loaded"
    finally:
        session.stop()
