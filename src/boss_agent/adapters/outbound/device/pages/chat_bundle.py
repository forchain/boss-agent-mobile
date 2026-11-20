"""
boss_agent.adapters.outbound.device.pages.chat_bundle
=====================================================
Production composition of the chat triage device world (ADR 0016).

``ChatTriage`` no longer constructs page objects; it receives a
:class:`~boss_agent.core.chat.interfaces.device.TriagePages` bundle of already-adapted
ports. This module is the one place that knows which concrete page classes fill that
bundle, so the engine's import graph stays free of Appium and a test can compose the
same shape from scripted stand-ins.
"""

from __future__ import annotations

from boss_agent.core.automation.interfaces.locator_store import LocatorStorePort
from boss_agent.core.automation.interfaces.ui_driver import UIDriverPort
from boss_agent.core.chat.interfaces.device import TriagePages

from .chat_adapters import ChatActorAdapter, CommunicationListAdapter
from .communication import ChatPage, CommunicationListPage
from .system import StartupDialogPage


def build_triage_world(driver: UIDriverPort, locator_store: LocatorStorePort) -> TriagePages:
    """Compose the device bundle one triage run drives from a live driver session.

    The startup dialog is dismissed here, before any page object is handed to a run:
    a dispatch can land on it, and a dialog left up would swallow the clicks the run
    is about to make.
    """
    startup_page = StartupDialogPage(driver, locator_registry=locator_store)
    if startup_page.is_dialog_present():
        startup_page.dismiss_dialog()
    return TriagePages(
        list_reader=CommunicationListAdapter(
            CommunicationListPage(driver, locator_registry=locator_store)
        ),
        chat_actor=ChatActorAdapter(ChatPage(driver, locator_registry=locator_store)),
    )
