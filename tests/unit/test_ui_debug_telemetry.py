"""
tests/unit/test_ui_debug_telemetry.py
=====================================
Unit tests for DEBUG-level UI operation telemetry (Issue #194).

Every concrete UI action (click, tap-at-point, type, swipe, back key, selector
lookup) must emit a standard `logging` DEBUG record under the dedicated
`droid_agent_core.ui` logger namespace, so developers can locate exactly which
UI call stalls by toggling the log level — without polluting business/broker logs.
"""

import logging
import re
import time
from unittest.mock import patch

import pytest

from boss_agent.pages import JobListPage, SearchPage
from droid_agent_core.gestures import HumanizedGestureExecutor, Point

UI_LOGGER = "droid_agent_core.ui"


@pytest.fixture(autouse=True)
def _no_sleep():
    """Neutralize humanized sleeps and polling waits so telemetry tests run fast."""
    with patch.object(time, "sleep", lambda *_: None):
        yield


class FakeElement:
    def __init__(self, class_name: str = "android.widget.ImageView"):
        self.rect = {"x": 100, "y": 200, "width": 80, "height": 60}
        self.class_name = class_name
        self.clicked = False
        self.typed_text = ""

    def click(self) -> None:
        self.clicked = True

    def send_keys(self, text: str) -> None:
        self.typed_text = text


class FakeDriver:
    def __init__(self, found_elements=None):
        self.taps: list = []
        self.swipes: list = []
        self.keycodes: list = []
        self.queried: list = []
        self._found = found_elements or []

    def tap(self, points, duration=0):
        self.taps.append((points, duration))

    def swipe(self, x1, y1, x2, y2, duration=0):
        self.swipes.append((x1, y1, x2, y2, duration))

    def press_keycode(self, code):
        self.keycodes.append(code)

    def find_elements(self, by, value):
        self.queried.append((by, value))
        return list(self._found)


def ui_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == UI_LOGGER]


@pytest.fixture
def ui_caplog(caplog):
    with caplog.at_level(logging.DEBUG, logger=UI_LOGGER):
        yield caplog


# ---------------------------------------------------------------------------
# HumanizedGestureExecutor telemetry
# ---------------------------------------------------------------------------


def test_human_click_emits_debug_log_with_element_and_coordinates(ui_caplog):
    driver = FakeDriver()
    executor = HumanizedGestureExecutor(driver)
    element = FakeElement()

    executor.human_click(element)

    records = ui_records(ui_caplog)
    assert any(
        "click" in r.getMessage().lower() and "ImageView" in r.getMessage() for r in records
    ), (
        f"expected a click debug entry describing the element, got: {[r.getMessage() for r in records]}"
    )
    assert any(re.search(r"target=\(\d", r.getMessage()) for r in records), (
        "expected tapped coordinates to be logged"
    )
    assert all(r.levelno == logging.DEBUG for r in records)


def test_human_click_at_point_logs_target_coordinates(ui_caplog):
    driver = FakeDriver()
    executor = HumanizedGestureExecutor(driver)

    executor.human_click_at_point(540.0, 1200.0)

    assert len(driver.taps) == 1
    records = [r for r in ui_records(ui_caplog) if "tap" in r.getMessage().lower()]
    assert any(
        re.search(r"\(\s*\d{2,4}\.?\d*\s*,\s*\d{2,4}\.?\d*\s*\)", r.getMessage()) for r in records
    ), f"expected coordinate pair in tap log, got: {[r.getMessage() for r in records]}"


def test_human_type_logs_text_length_without_raw_text(ui_caplog):
    driver = FakeDriver()
    executor = HumanizedGestureExecutor(driver)
    element = FakeElement()
    secret_text = "我的秘密口令123"

    executor.human_type(element, secret_text)

    assert element.typed_text == secret_text
    type_logs = [r.getMessage() for r in ui_records(ui_caplog) if "type" in r.getMessage().lower()]
    assert type_logs, "expected a type debug entry"
    assert all(f"len={len(secret_text)}" in msg for msg in type_logs), (
        f"expected text length in log, got: {type_logs}"
    )
    assert secret_text not in ui_caplog.text, "raw typed text must not leak into logs"


def test_human_swipe_logs_endpoints(ui_caplog):
    driver = FakeDriver()
    executor = HumanizedGestureExecutor(driver)

    executor.human_swipe(Point(540.0, 1800.0), Point(540.0, 600.0), duration_ms=500)

    assert len(driver.swipes) == 1
    swipe_logs = [
        r.getMessage() for r in ui_records(ui_caplog) if "swipe" in r.getMessage().lower()
    ]
    assert swipe_logs, "expected a swipe debug entry"
    assert any("->" in msg or "to=" in msg for msg in swipe_logs), (
        f"expected from->to endpoints in swipe log, got: {swipe_logs}"
    )


# ---------------------------------------------------------------------------
# BaseBossPage / press_back telemetry
# ---------------------------------------------------------------------------


def test_press_back_logs_keycode(ui_caplog):
    driver = FakeDriver()
    page = JobListPage(driver)

    page.press_back()

    assert driver.keycodes == [4]
    back_logs = [r.getMessage() for r in ui_records(ui_caplog) if "back" in r.getMessage().lower()]
    assert back_logs, "expected a back-key debug entry"
    assert any("4" in msg or "KEYCODE_BACK" in msg for msg in back_logs)


def test_press_back_telemetry_is_available_on_every_page_object(ui_caplog):
    """#194 lists press_back among BaseBossPage telemetry, not only the job list page."""
    driver = FakeDriver()
    page = SearchPage(driver)

    page.press_back()

    assert driver.keycodes == [4]
    assert any("KEYCODE_BACK" in r.getMessage() for r in ui_records(ui_caplog))


def test_find_by_key_logs_selector_value_and_duration(ui_caplog):
    driver = FakeDriver(found_elements=[FakeElement()])
    page = JobListPage(driver)

    elem = page.find_by_key("search.search_input", timeout_sec=0.0)

    assert elem is not None
    logs = [r.getMessage() for r in ui_records(ui_caplog)]
    assert any("et_search" in msg for msg in logs), (
        f"expected queried selector value in debug log, got: {logs}"
    )
    assert any(re.search(r"in \d+\.\d+s", msg) for msg in logs), (
        f"expected lookup duration in debug log, got: {logs}"
    )


def test_find_by_key_logs_miss_for_absent_element(ui_caplog):
    driver = FakeDriver(found_elements=[])
    page = JobListPage(driver)

    elem = page.find_by_key("job_list.search_icon", timeout_sec=0.0)

    assert elem is None
    logs = [r.getMessage() for r in ui_records(ui_caplog)]
    assert any("ly_menu" in msg for msg in logs), "every attempted selector must be logged"


def test_wait_for_key_logs_lookup(ui_caplog):
    driver = FakeDriver(found_elements=[FakeElement()])
    page = JobListPage(driver)

    elem = page.wait_for_key("search.search_input", timeout_sec=2.0)

    assert elem is not None
    assert ui_records(ui_caplog), "wait_for_key must emit UI debug telemetry"


# ---------------------------------------------------------------------------
# Level gating
# ---------------------------------------------------------------------------


def test_ui_debug_logs_are_silent_above_debug_level(caplog):
    with caplog.at_level(logging.INFO, logger=UI_LOGGER):
        driver = FakeDriver()
        executor = HumanizedGestureExecutor(driver)
        executor.human_click(FakeElement())

    assert ui_records(caplog) == [], (
        "UI telemetry must be DEBUG-only so it can be globally muted via log level"
    )
