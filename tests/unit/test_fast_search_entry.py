"""
tests/unit/test_fast_search_entry.py
====================================
Unit tests for the streamlined two-anchor search entry engine (Issue #195).

Engine contract (Spec #193):
  1. Search input box visible on current screen      -> already on search page, done.
  2. Search entry icon visible                        -> click it, wait for input.
  3. Neither                                          -> press hardware Back (0.5s apart),
     re-evaluate, bounded by a safety cap (default 10), re-activating Boss if the app
     lost foreground. NO probing of unrelated close/back buttons.
"""

import time
from unittest.mock import patch

import pytest

from boss_agent.pages import JobListPage
from droid_agent_core.locators import LocatorRegistry

EXACT_SEARCH_ICON_XPATH = (
    '//android.widget.LinearLayout[@resource-id="com.hpbr.bosszhipin:id/ly_menu"]'
    '/android.widget.RelativeLayout[2]'
    '/android.widget.ImageView[@resource-id="com.hpbr.bosszhipin:id/img_icon"]'
)


@pytest.fixture(autouse=True)
def _no_sleep():
    with patch.object(time, "sleep", lambda *_: None):
        yield


class FakeElement:
    def __init__(self, on_click=None):
        self.rect = {"x": 950, "y": 100, "width": 80, "height": 80}
        self.class_name = "android.widget.ImageView"
        self._on_click = on_click

    def click(self):
        if self._on_click:
            self._on_click()


class EntryDriver:
    """Simulates the Boss app screens with the two anchors: et_search input and ly_menu icon."""

    def __init__(self, icon_visible_after_backs=None, package="com.hpbr.bosszhipin"):
        self.keycodes: list[int] = []
        self.activated_count = 0
        self.queried: list[tuple[str, str]] = []
        self.icon_visible = icon_visible_after_backs is None
        self._icon_visible_after_backs = icon_visible_after_backs
        self.input_visible = False
        self.current_package = package

    # --- anchor elements ---------------------------------------------------
    def _icon(self):
        return [FakeElement(on_click=self._open_search_page)]

    def _open_search_page(self):
        self.input_visible = True

    def find_elements(self, by, value):
        self.queried.append((by, value))
        if "img_icon" in value or "ly_menu" in value:
            return self._icon() if self.icon_visible else []
        if "et_search" in value or "EditText" in value:
            return [FakeElement()] if self.input_visible else []
        return []

    def press_keycode(self, code):
        self.keycodes.append(code)
        if self._icon_visible_after_backs is not None:
            if len(self.keycodes) >= self._icon_visible_after_backs:
                self.icon_visible = True

    def activate_app(self, package):
        self.activated_count += 1
        self.current_package = package

    def get_window_size(self):
        return {"width": 1080, "height": 2400}

    def has_no_back_probe_of_secondary_buttons(self):
        banned = ("btn_cancel", "iv_close", "btn_back", "iv_back", "content-desc")
        return not any(b in v for _, v in self.queried for b in banned)


# ---------------------------------------------------------------------------
# Locator configuration
# ---------------------------------------------------------------------------


def test_search_icon_locator_is_single_exact_xpath():
    registry = LocatorRegistry(
        base_config_path="config/locators.yaml",
        local_config_path="config/__definitely_missing__.yaml",
    )
    selectors = registry.get_selectors("job_list.search_icon")
    assert len(selectors) == 1, (
        f"search_icon must be locked to exactly one verified XPath, got {len(selectors)}"
    )
    assert selectors[0].value == EXACT_SEARCH_ICON_XPATH


# ---------------------------------------------------------------------------
# open_search — three paths
# ---------------------------------------------------------------------------


def test_open_search_short_circuits_when_input_already_present():
    driver = EntryDriver()
    driver.input_visible = True
    page = JobListPage(driver)

    assert page.open_search(timeout_sec=5.0) is True
    assert driver.keycodes == [], "must not press Back when search input already visible"


def test_open_search_clicks_entry_icon_from_home():
    driver = EntryDriver()  # icon visible, input hidden (classic home screen)
    page = JobListPage(driver)

    assert page.open_search(timeout_sec=5.0) is True
    assert driver.input_visible is True, "clicking the entry icon must reach the search input"
    assert driver.keycodes == [], "must not press Back when the entry icon is visible"


def test_open_search_backtracks_from_subpage_until_entry_found():
    driver = EntryDriver(icon_visible_after_backs=3)
    page = JobListPage(driver)

    assert page.open_search(timeout_sec=5.0, max_back_attempts=10) is True
    assert driver.keycodes == [4, 4, 4], (
        f"expected exactly 3 hardware Back presses, got {driver.keycodes}"
    )
    assert driver.has_no_back_probe_of_secondary_buttons(), (
        "engine must not query irrelevant dialog-close / subpage back-button selectors"
    )


def test_open_search_backtracking_is_bounded():
    driver = EntryDriver(icon_visible_after_backs=None)
    driver.icon_visible = False  # never reaches home (e.g. stuck overlay)
    page = JobListPage(driver)

    assert page.open_search(timeout_sec=3.0, max_back_attempts=10) is False
    assert 1 <= len(driver.keycodes) <= 10, (
        f"back presses must stay within the safety cap, got {len(driver.keycodes)}"
    )


def test_open_search_reactivates_app_when_foreground_lost():
    driver = EntryDriver(icon_visible_after_backs=2, package="com.android.launcher")
    page = JobListPage(driver)

    assert page.open_search(timeout_sec=5.0) is True
    assert driver.activated_count >= 1, "must re-activate Boss when Back escapes the app"
