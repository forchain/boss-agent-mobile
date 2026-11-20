"""Unit tests for Bézier geometry, humanized gestures, and UI selectors."""

import logging
import time

import pytest

from boss_agent.adapters.memory.automation import FakeUIDriver, FakeUIElement
from boss_agent.core.automation.domain.geometry import (
    DOUBLE_TAP_MAX_INTERVAL,
    BézierTouchSynthesizer,
    Point,
    calculate_bounding_box_jitter,
    calculate_probe_coordinate,
    clamp_double_tap_interval,
)
from boss_agent.core.automation.domain.locators import By, UISelector
from boss_agent.core.automation.use_cases import gestures
from boss_agent.core.automation.use_cases.gestures import HumanizedGestureExecutor

UI_LOGGER = "droid_agent_core.ui"

ANDROID_DOUBLE_TAP_MAX_INTERVAL = DOUBLE_TAP_MAX_INTERVAL


# ---------------------------------------------------------------------------
# Geometry and selectors
# ---------------------------------------------------------------------------


def test_bezier_curve_generation():
    start = Point(100.0, 500.0)
    end = Point(100.0, 100.0)
    points = BézierTouchSynthesizer.generate_curve(start, end, steps=10)

    assert len(points) == 11
    assert points[0].x == pytest.approx(100.0)
    assert points[0].y == pytest.approx(500.0)
    assert points[-1].x == pytest.approx(100.0)
    assert points[-1].y == pytest.approx(100.0)
    for p in points:
        assert isinstance(p.x, float)
        assert isinstance(p.y, float)


def test_bounding_box_jitter():
    x, y = calculate_bounding_box_jitter(left=100, top=200, width=50, height=30, jitter_factor=0.2)
    assert 100 <= x <= 150
    assert 200 <= y <= 230


def test_probe_coordinate_origin_and_clamping():
    rect = {"x": 100, "y": 200, "width": 400, "height": 500}
    assert calculate_probe_coordinate(rect, [0.90, 20], origin="bottom-left") == (460.0, 680.0)
    assert calculate_probe_coordinate(rect, [50, 60], origin="bottom-left") == (150.0, 640.0)
    assert calculate_probe_coordinate(rect, [150, 150], origin="bottom-left") == (250.0, 550.0)

    tight = {"x": 100, "y": 200, "width": 100, "height": 100}
    assert calculate_probe_coordinate(tight, [150, 150], origin="bottom-left") == (198.0, 202.0)


def test_clamp_double_tap_interval_stays_inside_android_window():
    for _ in range(50):
        interval = clamp_double_tap_interval((0.9, 1.4))
        assert 0 < interval < ANDROID_DOUBLE_TAP_MAX_INTERVAL


def test_ui_selector_resolution():
    sel_id = UISelector(by=By.ID, value="com.example.app:id/submit_btn")
    assert sel_id.by == By.ID
    assert sel_id.value == "com.example.app:id/submit_btn"

    sel_text = UISelector(by=By.XPATH, value="//android.widget.TextView[@text='Continue']")
    assert "TextView" in sel_text.value


# ---------------------------------------------------------------------------
# Humanized gestures (ticket #387)
# ---------------------------------------------------------------------------


ELEMENT_RECT = {"x": 100, "y": 200, "width": 80, "height": 60}


class FakeElement(FakeUIElement):
    """``FakeUIElement`` with the bounds and class name this file's assertions expect."""

    def __init__(self, rect: dict | None = ELEMENT_RECT) -> None:
        super().__init__(rect=rect, class_name="android.widget.LinearLayout")


class RecordingTime:
    """`time` proxy that records sleep durations instead of waiting."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def __getattr__(self, name: str):
        return getattr(time, name)


@pytest.fixture
def recorded_sleeps(monkeypatch) -> RecordingTime:
    """Replace the tier's instant-pacing clock with one that records durations."""
    recorder = RecordingTime()
    monkeypatch.setattr(gestures, "time", recorder)
    return recorder


def test_human_double_click_prefers_the_driver_pointer_batch():
    driver = FakeUIDriver(double_tap_supported=True)
    HumanizedGestureExecutor(driver).human_double_click(FakeElement())

    assert len(driver.double_taps) == 1
    assert driver.taps == [], "the pointer batch must not also fall back to driver.tap"


def test_human_double_click_falls_back_to_driver_tap_without_pointer_batch(recorded_sleeps):
    driver = FakeUIDriver(double_tap_supported=False)
    HumanizedGestureExecutor(driver).human_double_click(FakeElement())

    assert len(driver.taps) == 2, "fallback must emit exactly two taps"
    for positions, _duration in driver.taps:
        ((x, y),) = positions
        assert 100 <= x <= 180
        assert 200 <= y <= 260


def test_human_double_click_falls_back_to_element_click_without_coordinates():
    element = FakeElement(rect=None)
    HumanizedGestureExecutor(FakeUIDriver(double_tap_supported=False)).human_double_click(
        element, jitter=False
    )
    assert element.click_count == 2


def test_human_double_click_at_point_prefers_the_driver_pointer_batch():
    driver = FakeUIDriver(double_tap_supported=True)
    HumanizedGestureExecutor(driver).human_double_click_at_point(540.0, 1200.0)

    assert len(driver.double_taps) == 1
    assert driver.taps == []


def test_human_double_click_at_point_falls_back_to_sequential_driver_tap(recorded_sleeps):
    driver = FakeUIDriver(double_tap_supported=False)
    HumanizedGestureExecutor(driver).human_double_click_at_point(540.0, 1200.0)

    assert len(driver.taps) == 2
    for positions, _duration in driver.taps:
        ((x, y),) = positions
        assert abs(x - 540.0) <= 12.0
        assert abs(y - 1200.0) <= 12.0


def test_human_double_click_keeps_inter_tap_interval_inside_android_window():
    driver = FakeUIDriver(double_tap_supported=True)
    HumanizedGestureExecutor(driver).human_double_click(FakeElement(), interval_range=(0.12, 0.18))

    (_x, _y, pause) = driver.double_taps[0]
    assert 0.12 <= pause <= 0.18
    assert pause < ANDROID_DOUBLE_TAP_MAX_INTERVAL


def test_human_double_click_fallback_interval_stays_inside_android_window(recorded_sleeps):
    driver = FakeUIDriver(double_tap_supported=False)
    HumanizedGestureExecutor(driver).human_double_click(FakeElement(), interval_range=(0.12, 0.18))

    inter_tap = recorded_sleeps.sleeps[0]
    assert 0.12 <= inter_tap <= 0.18
    assert inter_tap < ANDROID_DOUBLE_TAP_MAX_INTERVAL


def test_human_double_click_clamps_interval_above_android_double_tap_window(recorded_sleeps):
    HumanizedGestureExecutor(FakeUIDriver(double_tap_supported=False)).human_double_click(
        FakeElement(), interval_range=(0.9, 1.4)
    )

    inter_tap = recorded_sleeps.sleeps[0]
    assert 0 < inter_tap < ANDROID_DOUBLE_TAP_MAX_INTERVAL


def test_human_double_click_jitter_stays_inside_element_bounds():
    driver = FakeUIDriver(double_tap_supported=False)
    for _ in range(25):
        HumanizedGestureExecutor(driver).human_double_click(FakeElement())

    assert driver.taps, "expected jittered taps"
    for positions, _duration in driver.taps:
        ((x, y),) = positions
        assert 100 <= x <= 180
        assert 200 <= y <= 260
    for first, second in zip(driver.taps[0::2], driver.taps[1::2], strict=True):
        assert first[0] == second[0]


def test_human_double_click_at_point_jitter_is_bounded():
    driver = FakeUIDriver(double_tap_supported=False)
    for _ in range(25):
        HumanizedGestureExecutor(driver).human_double_click_at_point(540.0, 1200.0, jitter_px=3.0)

    xs = [positions[0][0] for positions, _duration in driver.taps]
    ys = [positions[0][1] for positions, _duration in driver.taps]
    assert max(xs) - min(xs) > 0, "expected real micro-jitter rather than a fixed point"
    assert all(abs(x - 540.0) <= 15.0 for x in xs)
    assert all(abs(y - 1200.0) <= 15.0 for y in ys)


def test_human_double_click_emits_debug_telemetry(caplog):
    with caplog.at_level(logging.DEBUG, logger=UI_LOGGER):
        HumanizedGestureExecutor(FakeUIDriver(double_tap_supported=False)).human_double_click(
            FakeElement()
        )

    records = [r for r in caplog.records if r.name == UI_LOGGER]
    assert records, "double-click must emit UI debug telemetry"
    assert all(r.levelno == logging.DEBUG for r in records)
    messages = [r.getMessage() for r in records]
    assert any(
        m.startswith("[UI] double_click element=") and "LinearLayout" in m for m in messages
    ), f"expected an element-scoped double_click entry, got: {messages}"


def test_human_double_click_is_a_noop_without_driver_or_element():
    driver = FakeUIDriver(double_tap_supported=False)
    HumanizedGestureExecutor(driver).human_double_click(None)
    HumanizedGestureExecutor(None).human_double_click(FakeElement())
    HumanizedGestureExecutor(None).human_double_click_at_point(540.0, 1200.0)

    assert driver.taps == []
    assert driver.double_taps == []


def test_human_type_clears_and_types():
    element = FakeElement()
    HumanizedGestureExecutor(FakeUIDriver(double_tap_supported=False)).human_type(
        element, "hello", clear_first=True
    )
    assert element.cleared is True
    assert element.typed_text == "hello"


def test_human_swipe_uses_bezier_endpoints():
    driver = FakeUIDriver(double_tap_supported=False)
    HumanizedGestureExecutor(driver).human_swipe(Point(100.0, 500.0), Point(100.0, 100.0))
    assert driver.swipes
    x1, y1, x2, y2, _duration = driver.swipes[0]
    assert (x1, y1) == (100, 500)
    assert (x2, y2) == (100, 100)


def test_random_sleep_records_humanized_delay(recorded_sleeps):
    HumanizedGestureExecutor().random_sleep(0.5, 0.6)
    assert len(recorded_sleeps.sleeps) == 1
    assert 0.5 <= recorded_sleeps.sleeps[0] <= 0.6
