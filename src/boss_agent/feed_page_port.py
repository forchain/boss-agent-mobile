"""
boss_agent.feed_page_port
=========================
The navigation seam between the Mobile Job Feed Pipeline and Android page objects.

``JobFeedPort`` is a structural Protocol over the existing page vocabulary: search
entry, viewport reads, scrolling, detail navigation, description expansion, location
line, distance probe, contact-button state and communication dispatch. Following the
Chat Triage port precedent (ADR 0016), there are no wrapper classes around the page
objects — the production adapter owns the five page instances, and a test fake owns a
scripted in-memory feed instead.
"""

from typing import Any, Protocol

from .job_entities import JobPosting
from .pages.communication import ChatPage
from .pages.job_detail import JobDetailPage
from .pages.job_feed import (
    FilterDialogPage,
    IndustryFilterDialogPage,
    JobListPage,
    LocatedJobCard,
    SearchPage,
)
from .pages.system import StartupDialogPage


class FeedPagePort(Protocol):
    """Every navigation verb the feed pipeline needs, in page vocabulary."""

    def is_startup_dialog_present(self) -> bool: ...

    def dismiss_startup_dialog(self) -> None: ...

    def navigate_to_home(self) -> bool: ...

    def open_search(self, timeout_sec: float) -> bool: ...

    def is_search_page(self) -> bool: ...

    def search(self, keyword: str, timeout_sec: float) -> bool: ...

    def get_feed_bottom_boundary(self) -> Any | None: ...

    def is_feed_bottom_reached(self) -> bool | None: ...

    def extract_visible_job_cards(self, max_cards: int) -> list[LocatedJobCard]: ...

    def scroll_job_list(self) -> None: ...

    def select_first_job(self, timeout_sec: float) -> bool: ...

    def navigate_back(self) -> None: ...

    def navigate_back_from_chat(self) -> None: ...

    def extract_commute_distance(self) -> tuple[float | None, str]: ...

    def get_chat_button_state(self) -> Any: ...

    def extract_job_posting(self, **kwargs: Any) -> JobPosting: ...

    def open_chat(self, timeout_sec: float) -> bool: ...

    def type_greeting_message(self, greeting: str, timeout_sec: float) -> Any: ...

    def click_send(self, timeout_sec: float) -> bool: ...

    def apply_industry_filters(self, industries: list[str], timeout_sec: float) -> None: ...

    def apply_filters(self, filter_config: Any, timeout_sec: float) -> None: ...

    def clear_filters(self, timeout_sec: float) -> None: ...


class AppiumFeedPagePort:
    """Production adapter: the five Appium page objects behind the port verbs."""

    def __init__(self, driver: Any) -> None:
        self.driver = driver
        self.startup_page = StartupDialogPage(driver)
        self.list_page = JobListPage(driver)
        self.search_page = SearchPage(driver)
        self.detail_page = JobDetailPage(driver)
        self.chat_page = ChatPage(driver)

    # Startup
    def is_startup_dialog_present(self) -> bool:
        return self.startup_page.is_dialog_present()

    def dismiss_startup_dialog(self) -> None:
        self.startup_page.dismiss_dialog()

    # Search entry
    def navigate_to_home(self) -> bool:
        return self.list_page.navigate_to_home()

    def open_search(self, timeout_sec: float) -> bool:
        return self.list_page.open_search(timeout_sec=timeout_sec)

    def is_search_page(self) -> bool:
        return self.search_page.is_search_page()

    def search(self, keyword: str, timeout_sec: float) -> bool:
        return self.search_page.search(keyword, timeout_sec=timeout_sec)

    # Viewport iteration
    def get_feed_bottom_boundary(self) -> Any | None:
        return self.list_page.get_feed_bottom_boundary()

    def is_feed_bottom_reached(self) -> bool | None:
        return self.list_page.is_feed_bottom_reached()

    def extract_visible_job_cards(self, max_cards: int) -> list[LocatedJobCard]:
        return self.list_page.extract_visible_job_cards(max_cards=max_cards)

    def scroll_job_list(self) -> None:
        self.list_page.scroll_job_list()

    def select_first_job(self, timeout_sec: float) -> bool:
        return self.list_page.select_first_job(timeout_sec=timeout_sec)

    # Detail
    def navigate_back(self) -> None:
        self.detail_page.navigate_back()

    def navigate_back_from_chat(self) -> None:
        self.chat_page.navigate_back()

    def extract_commute_distance(self) -> tuple[float | None, str]:
        return self.detail_page.extract_commute_distance()

    def get_chat_button_state(self) -> Any:
        return self.detail_page.get_chat_button_state()

    def extract_job_posting(self, **kwargs: Any) -> JobPosting:
        return self.detail_page.extract_job_posting(**kwargs)

    def open_chat(self, timeout_sec: float) -> bool:
        return self.detail_page.open_chat(timeout_sec=timeout_sec)

    # Communication dispatch
    def type_greeting_message(self, greeting: str, timeout_sec: float) -> Any:
        return self.chat_page.type_greeting_message(greeting, timeout_sec=timeout_sec)

    def click_send(self, timeout_sec: float) -> bool:
        return self.chat_page.click_send(timeout_sec=timeout_sec)

    # Filters
    def apply_industry_filters(self, industries: list[str], timeout_sec: float) -> None:
        IndustryFilterDialogPage(self.driver).apply_industry_filters(
            industries, timeout_sec=timeout_sec
        )

    def apply_filters(self, filter_config: Any, timeout_sec: float) -> None:
        FilterDialogPage(self.driver).apply_filters(filter_config, timeout_sec=timeout_sec)

    def clear_filters(self, timeout_sec: float) -> None:
        FilterDialogPage(self.driver).clear_filters(timeout_sec=timeout_sec)
