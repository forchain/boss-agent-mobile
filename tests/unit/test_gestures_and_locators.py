"""Unit tests for Bézier gesture synthesizer, double-tap gestures, and UI selectors."""

import logging
import time

import pytest

from boss_agent.automation import gestures
from boss_agent.automation.gestures import (
    DOUBLE_TAP_MAX_INTERVAL,
    BézierTouchSynthesizer,
    HumanizedGestureExecutor,
    Point,
    calculate_bounding_box_jitter,
)
from boss_agent.automation.locators import By, UISelector

UI_LOGGER = "boss_agent.automation.ui"

# Android's system double-tap detector accepts a second tap only inside this
# window; past it the input stream is read as two independent single taps. The
# value lives in the module under test — a copy here could drift from the clamp
# it is meant to police.
ANDROID_DOUBLE_TAP_MAX_INTERVAL = DOUBLE_TAP_MAX_INTERVAL


def test_bezier_curve_generation():
    start = Point(100.0, 500.0)
    end = Point(100.0, 100.0)
    points = BézierTouchSynthesizer.generate_curve(start, end, steps=10)

    assert len(points) == 11
    assert points[0].x == pytest.approx(100.0)
    assert points[0].y == pytest.approx(500.0)
    assert points[-1].x == pytest.approx(100.0)
    assert points[-1].y == pytest.approx(100.0)

    # Ensure non-trivial trajectory (intermediate points have valid coordinates)
    for p in points:
        assert isinstance(p.x, float)
        assert isinstance(p.y, float)


def test_bounding_box_jitter():
    # Bounding box: left=100, top=200, width=50, height=30
    x, y = calculate_bounding_box_jitter(left=100, top=200, width=50, height=30, jitter_factor=0.2)

    # Must be strictly within bounds
    assert 100 <= x <= 150
    assert 200 <= y <= 230


def test_ui_selector_resolution():
    sel_id = UISelector(by=By.ID, value="com.example.app:id/submit_btn")
    assert sel_id.by == By.ID
    assert sel_id.value == "com.example.app:id/submit_btn"

    sel_text = UISelector(by=By.XPATH, value="//android.widget.TextView[@text='Continue']")
    assert "TextView" in sel_text.value


# ---------------------------------------------------------------------------
# Humanized double-click / double-tap (ticket #387)
#
# The W3C pointer batch itself belongs to the adapter and is covered in
# `test_appium_adapter.py`. These tests drive the neutral fallbacks.
# ---------------------------------------------------------------------------


class FakeElement:
    """Mobile element stand-in exposing only the attributes the executor reads."""

    def __init__(self, rect: dict | None = None) -> None:
        self.rect = rect if rect is not None else {"x": 100, "y": 200, "width": 80, "height": 60}
        self.class_name = "android.widget.LinearLayout"
        self.click_count = 0

    def click(self) -> None:
        self.click_count += 1


class FakeDriver:
    """Driver stand-in with only the legacy `tap` endpoint available."""

    def __init__(self) -> None:
        self.taps: list = []

    def tap(self, positions, duration=0):
        self.taps.append((list(positions), duration))


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


def test_human_double_click_falls_back_to_driver_tap_without_pointer_batch(recorded_sleeps):
    driver = FakeDriver()

    HumanizedGestureExecutor(driver).human_double_click(FakeElement())

    assert len(driver.taps) == 2, "fallback must emit exactly two taps"
    for positions, _duration in driver.taps:
        ((x, y),) = positions
        assert 100 <= x <= 180
        assert 200 <= y <= 260


def test_human_double_click_falls_back_to_element_click_without_coordinates():
    element = FakeElement(rect=None)

    HumanizedGestureExecutor(FakeDriver()).human_double_click(element, jitter=False)

    assert element.click_count == 2


def test_human_double_click_at_point_falls_back_to_sequential_driver_tap(recorded_sleeps):
    driver = FakeDriver()

    HumanizedGestureExecutor(driver).human_double_click_at_point(540.0, 1200.0)

    assert len(driver.taps) == 2
    for positions, _duration in driver.taps:
        ((x, y),) = positions
        # Default jitter_px=3.0 keeps the fallback tap near the requested point.
        assert abs(x - 540.0) <= 12.0
        assert abs(y - 1200.0) <= 12.0


def test_human_double_click_fallback_interval_stays_inside_android_window(recorded_sleeps):
    HumanizedGestureExecutor(FakeDriver()).human_double_click(
        FakeElement(), interval_range=(0.12, 0.18)
    )

    # The inter-tap gap is paced before the trailing settle pause, so it comes first.
    inter_tap = recorded_sleeps.sleeps[0]
    assert 0.12 <= inter_tap <= 0.18
    assert inter_tap < ANDROID_DOUBLE_TAP_MAX_INTERVAL


def test_human_double_click_clamps_interval_above_android_double_tap_window(recorded_sleeps):
    """A caller asking for a too-slow gap must still land inside the detector window."""

    HumanizedGestureExecutor(FakeDriver()).human_double_click(
        FakeElement(), interval_range=(0.9, 1.4)
    )

    inter_tap = recorded_sleeps.sleeps[0]
    assert 0 < inter_tap < ANDROID_DOUBLE_TAP_MAX_INTERVAL


def test_human_double_click_jitter_stays_inside_element_bounds():
    driver = FakeDriver()

    for _ in range(25):
        HumanizedGestureExecutor(driver).human_double_click(FakeElement())

    assert driver.taps, "expected jittered taps"
    for positions, _duration in driver.taps:
        ((x, y),) = positions
        assert 100 <= x <= 180
        assert 200 <= y <= 260

    # Both taps share one jittered point, as a real finger does across the window.
    for first, second in zip(driver.taps[0::2], driver.taps[1::2], strict=True):
        assert first[0] == second[0]


def test_human_double_click_at_point_jitter_is_bounded(recorded_sleeps):
    driver = FakeDriver()

    for _ in range(25):
        HumanizedGestureExecutor(driver).human_double_click_at_point(540.0, 1200.0, jitter_px=3.0)

    xs = [positions[0][0] for positions, _duration in driver.taps]
    ys = [positions[0][1] for positions, _duration in driver.taps]
    assert max(xs) - min(xs) > 0, "expected real micro-jitter rather than a fixed point"
    assert all(abs(x - 540.0) <= 15.0 for x in xs)
    assert all(abs(y - 1200.0) <= 15.0 for y in ys)


def test_human_double_click_emits_debug_telemetry(caplog):
    with caplog.at_level(logging.DEBUG, logger=UI_LOGGER):
        HumanizedGestureExecutor(FakeDriver()).human_double_click(FakeElement())

    records = [r for r in caplog.records if r.name == UI_LOGGER]
    assert records, "double-click must emit UI debug telemetry"
    assert all(r.levelno == logging.DEBUG for r in records)
    messages = [r.getMessage() for r in records]
    assert any(
        m.startswith("[UI] double_click element=") and "LinearLayout" in m for m in messages
    ), f"expected an element-scoped double_click entry, got: {messages}"


def test_human_double_click_is_a_noop_without_driver_or_element():
    driver = FakeDriver()
    HumanizedGestureExecutor(driver).human_double_click(None)
    HumanizedGestureExecutor(None).human_double_click(FakeElement())
    HumanizedGestureExecutor(None).human_double_click_at_point(540.0, 1200.0)

    assert driver.taps == []
