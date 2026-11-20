"""
boss_agent.core.job.interfaces.device
=====================================
The feed engine's device world, expressed structurally (ADR 0013, ADR 0016).

``JobFeedPipeline`` used to construct six page objects from a driver, which made every
import of the engine drag Appium into the core import graph and made a scripted device
impossible to inject without patching constructors. This module inverts that dependency:
the pipeline names exactly the operations it uses, and the production bundle in
``boss_agent.adapters.outbound.device.pages.feed_bundle`` composes the concrete page
objects that satisfy them.

Structural typing, not wrappers (the Chat Triage precedent): the real page classes
already speak this vocabulary, so they satisfy these Protocols as they are.

The filter dialogs are part of the bundle because the pipeline drives them too; the
pipeline's public page attributes remain the five it has always exposed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from boss_agent.core.job.domain.job_entities import JobCardBrief, JobPosting
from boss_agent.core.shared.enums import ChatButtonState


@dataclass(frozen=True)
class FeedBoundary:
    """Bottom boundary banner state reported by the job list screen."""

    visible: bool = False
    y_position: float | None = None


@runtime_checkable
class FeedCardHandle(Protocol):
    """A parsed card brief paired with its opaque screen handle and optional Y coordinate."""

    card: JobCardBrief

    @property
    def y_position(self) -> float | None:
        """Viewport vertical coordinate if readable, otherwise ``None``."""


class StartupDialogPageProtocol(Protocol):
    """The app's startup dialog: present-or-not, then dismiss."""

    def is_dialog_present(self) -> bool: ...

    def dismiss_dialog(self) -> Any: ...


class JobListPageProtocol(Protocol):
    """The search-result viewport: entry, cards, boundary, card opening and pagination."""

    def navigate_to_home(self) -> Any: ...

    def open_search(self, timeout_sec: float = ...) -> bool: ...

    def get_feed_boundary(self) -> FeedBoundary: ...

    def is_card_below_boundary(self, card: FeedCardHandle, boundary: FeedBoundary) -> bool: ...

    def extract_visible_job_cards(self, max_cards: int = ...) -> list[FeedCardHandle]: ...

    def open_card(
        self, card: FeedCardHandle | None = None, *, timeout_sec: float = ...
    ) -> bool: ...

    def select_first_job(self, timeout_sec: float = ...) -> bool: ...

    def is_feed_bottom_reached(self) -> bool | None: ...

    def scroll_job_list(self) -> None: ...


class SearchPageProtocol(Protocol):
    """Keyword entry on the search screen."""

    def is_search_page(self) -> bool: ...

    def search(self, keyword: str, timeout_sec: float = ...) -> bool: ...


class JobDetailPageProtocol(Protocol):
    """One posting's detail screen: contact state, JD, commute probe and chat entry."""

    def navigate_back(self) -> Any: ...

    def get_chat_button_state(self) -> ChatButtonState: ...

    def extract_commute_distance(self) -> tuple[float | None, str]: ...

    def extract_job_posting(self, **kwargs: Any) -> JobPosting: ...

    def open_chat(self, timeout_sec: float = ...) -> bool: ...


class ChatPageProtocol(Protocol):
    """The greeting composer."""

    def type_greeting_message(self, greeting: str, timeout_sec: float = ...) -> Any: ...

    def click_send(self, timeout_sec: float = ...) -> bool: ...

    def navigate_back(self) -> Any: ...


class FilterDialogPageProtocol(Protocol):
    """The app's general filter dialog."""

    def apply_filters(self, config: Any | None, timeout_sec: float = ...) -> bool: ...

    def clear_filters(self, timeout_sec: float = ...) -> bool: ...


class IndustryFilterDialogPageProtocol(Protocol):
    """The engine's own multi-select industry dialog."""

    def apply_industry_filters(
        self, industries: list[str], timeout_sec: float = ...
    ) -> list[str]: ...


@dataclass(frozen=True)
class FeedDeviceWorld:
    """The composed device surface one feed run drives.

    Production composition lives in
    ``boss_agent.adapters.outbound.device.pages.feed_bundle``. Tests compose the
    same shape from scripted stand-ins, which is the seam that keeps the engine out of
    Appium's import graph.
    """

    startup_page: StartupDialogPageProtocol
    list_page: JobListPageProtocol
    search_page: SearchPageProtocol
    detail_page: JobDetailPageProtocol
    chat_page: ChatPageProtocol
    filter_page: FilterDialogPageProtocol
    industry_filter_page: IndustryFilterDialogPageProtocol


#: The station-aware probe closure the detail page accepts (issue #333).
CommuteProbeUpgrade = Callable[[str], bool]
