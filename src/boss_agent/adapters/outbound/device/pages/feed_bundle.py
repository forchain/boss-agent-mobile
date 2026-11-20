"""
boss_agent.adapters.outbound.device.pages.feed_bundle
=====================================================
Production composition of the feed engine's device world (ADR 0016).

``JobFeedPipeline`` no longer constructs page objects; it receives a
:class:`~boss_agent.core.job.interfaces.device.FeedDeviceWorld` and drives only the
operations it declares. This module is the one place that knows which concrete page
classes fill that bundle, so the engine's import graph stays free of Appium and a test
can compose the same shape from scripted stand-ins.

The page classes are imported from the submodule that owns each one (never the facade).
"""

from __future__ import annotations

from boss_agent.core.automation.interfaces.locator_store import LocatorStorePort
from boss_agent.core.automation.interfaces.ui_driver import UIDriverPort
from boss_agent.core.job.interfaces.device import FeedDeviceWorld

from .communication import ChatPage
from .job_detail import JobDetailPage
from .job_feed import FilterDialogPage, IndustryFilterDialogPage, JobListPage, SearchPage
from .system import StartupDialogPage


def build_feed_device_world(
    driver: UIDriverPort, locator_store: LocatorStorePort
) -> FeedDeviceWorld:
    """Compose the page bundle one feed run drives from a live driver session."""
    return FeedDeviceWorld(
        startup_page=StartupDialogPage(driver, locator_registry=locator_store),
        list_page=JobListPage(driver, locator_registry=locator_store),
        search_page=SearchPage(driver, locator_registry=locator_store),
        detail_page=JobDetailPage(driver, locator_registry=locator_store),
        chat_page=ChatPage(driver, locator_registry=locator_store),
        filter_page=FilterDialogPage(driver, locator_registry=locator_store),
        industry_filter_page=IndustryFilterDialogPage(driver, locator_registry=locator_store),
    )
