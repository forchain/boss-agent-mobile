"""Unit tests for Bézier gesture synthesizer, double-tap gestures, and UI selectors."""

import logging
import time

import pytest

from droid_agent_core import gestures
from droid_agent_core.gestures import (
    DOUBLE_TAP_MAX_INTERVAL,
    BézierTouchSynthesizer,
    HumanizedGestureExecutor,
    Point,
    calculate_bounding_box_jitter,
)
from droid_agent_core.locators import By, UISelector

UI_LOGGER = "droid_agent_core.ui"

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
    """Appium driver stand-in with only the legacy `tap` endpoint available."""

    def __init__(self) -> None:
        self.taps: list = []

    def tap(self, positions, duration=0):
        self.taps.append((list(positions), duration))


class FakePointer:
    """Chainable stand-in for Selenium's W3C pointer action builder."""

    def __init__(self, calls: list) -> None:
        self._calls = calls

    def _record(self, name: str, *args) -> "FakePointer":
        self._calls.append((name, *args))
        return self

    def move_to_location(self, x, y, *_args, **_kwargs) -> "FakePointer":
        return self._record("move_to_location", x, y)

    def pointer_down(self, button=0, *_args, **_kwargs) -> "FakePointer":
        return self._record("pointer_down", button)

    def pointer_up(self, button=0) -> "FakePointer":
        return self._record("pointer_up", button)

    def pause(self, seconds=0) -> "FakePointer":
        return self._record("pause", seconds)


class FakeW3CActions:
    """Stand-in for the `ActionBuilder` reachable at `ActionChains.w3c_actions`."""

    def __init__(self, pointer: FakePointer) -> None:
        self.pointer_action = pointer


class FakeActionChains:
    """Stand-in for `ActionChains`: records pointer steps and each `perform()` batch."""

    batches: list = []
    pointer_calls: list = []

    def __init__(self, driver) -> None:
        self.driver = driver
        self.pointer = FakePointer(type(self).pointer_calls)
        self.w3c_actions = FakeW3CActions(self.pointer)

    def perform(self) -> None:
        type(self).batches.append(list(type(self).pointer_calls))
        type(self).pointer_calls = []


class RaisingActionChains(FakeActionChains):
    """W3C batch that always fails, standing in for a driver without pointer support."""

    def perform(self) -> None:
        raise RuntimeError("unhandled inspector error: W3C actions unsupported")


@pytest.fixture
def w3c_supported(monkeypatch):
    """Make the W3C pointer seam available and hand back the recorded batches."""
    FakeActionChains.batches = []
    FakeActionChains.pointer_calls = []
    monkeypatch.setattr(gestures, "_load_w3c_action_chains", lambda: FakeActionChains)
    return FakeActionChains


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


def test_human_double_click_prefers_w3c_pointer_actions_in_one_batch(w3c_supported):
    driver = FakeDriver()
    executor = HumanizedGestureExecutor(driver)

    executor.human_double_click(FakeElement())

    # One batch carries both taps, so no HTTP round trip sits between them.
    assert len(w3c_supported.batches) == 1
    steps = [name for name, *_rest in w3c_supported.batches[0]]
    assert steps == [
        "move_to_location",
        "pointer_down",
        "pointer_up",
        "pause",
        "pointer_down",
        "pointer_up",
    ]
    assert driver.taps == [], "the W3C path must not also fall back to driver.tap"


def test_human_double_click_falls_back_to_driver_tap_without_action_chains(
    monkeypatch, recorded_sleeps
):
    monkeypatch.setattr(gestures, "_load_w3c_action_chains", lambda: None)
    driver = FakeDriver()

    HumanizedGestureExecutor(driver).human_double_click(FakeElement())

    assert len(driver.taps) == 2, "fallback must emit exactly two taps"
    for positions, _duration in driver.taps:
        ((x, y),) = positions
        assert 100 <= x <= 180
        assert 200 <= y <= 260


def test_human_double_click_falls_back_to_element_click_without_coordinates(monkeypatch):
    monkeypatch.setattr(gestures, "_load_w3c_action_chains", lambda: None)
    element = FakeElement(rect=None)

    HumanizedGestureExecutor(FakeDriver()).human_double_click(element, jitter=False)

    assert element.click_count == 2


def test_human_double_click_falls_back_when_w3c_batch_raises(monkeypatch):
    monkeypatch.setattr(gestures, "_load_w3c_action_chains", lambda: RaisingActionChains)
    driver = FakeDriver()

    HumanizedGestureExecutor(driver).human_double_click(FakeElement())

    assert len(driver.taps) == 2, "a failed W3C batch must degrade to driver.tap"


def test_human_double_click_at_point_prefers_w3c_pointer_actions(w3c_supported):
    driver = FakeDriver()

    HumanizedGestureExecutor(driver).human_double_click_at_point(540.0, 1200.0)

    assert len(w3c_supported.batches) == 1
    moves = [args for name, *args in w3c_supported.batches[0] if name == "move_to_location"]
    assert len(moves) == 1
    assert driver.taps == []


def test_human_double_click_at_point_falls_back_to_sequential_driver_tap(
    monkeypatch, recorded_sleeps
):
    monkeypatch.setattr(gestures, "_load_w3c_action_chains", lambda: None)
    driver = FakeDriver()

    HumanizedGestureExecutor(driver).human_double_click_at_point(540.0, 1200.0)

    assert len(driver.taps) == 2
    for positions, _duration in driver.taps:
        ((x, y),) = positions
        # Default jitter_px=3.0 keeps the fallback tap near the requested point.
        assert abs(x - 540.0) <= 12.0
        assert abs(y - 1200.0) <= 12.0


def test_human_double_click_keeps_inter_tap_interval_inside_android_window(w3c_supported):
    HumanizedGestureExecutor(FakeDriver()).human_double_click(
        FakeElement(), interval_range=(0.12, 0.18)
    )

    (pause,) = [step[1] for step in w3c_supported.batches[0] if step[0] == "pause"]
    assert 0.12 <= pause <= 0.18
    assert pause < ANDROID_DOUBLE_TAP_MAX_INTERVAL


def test_human_double_click_fallback_interval_stays_inside_android_window(
    monkeypatch, recorded_sleeps
):
    monkeypatch.setattr(gestures, "_load_w3c_action_chains", lambda: None)

    HumanizedGestureExecutor(FakeDriver()).human_double_click(
        FakeElement(), interval_range=(0.12, 0.18)
    )

    # The inter-tap gap is paced before the trailing settle pause, so it comes first.
    inter_tap = recorded_sleeps.sleeps[0]
    assert 0.12 <= inter_tap <= 0.18
    assert inter_tap < ANDROID_DOUBLE_TAP_MAX_INTERVAL


def test_human_double_click_clamps_interval_above_android_double_tap_window(
    monkeypatch, recorded_sleeps
):
    """A caller asking for a too-slow gap must still land inside the detector window."""
    monkeypatch.setattr(gestures, "_load_w3c_action_chains", lambda: None)

    HumanizedGestureExecutor(FakeDriver()).human_double_click(
        FakeElement(), interval_range=(0.9, 1.4)
    )

    inter_tap = recorded_sleeps.sleeps[0]
    assert 0 < inter_tap < ANDROID_DOUBLE_TAP_MAX_INTERVAL


def test_human_double_click_jitter_stays_inside_element_bounds(monkeypatch):
    monkeypatch.setattr(gestures, "_load_w3c_action_chains", lambda: None)
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


def test_human_double_click_at_point_jitter_is_bounded(monkeypatch, recorded_sleeps):
    monkeypatch.setattr(gestures, "_load_w3c_action_chains", lambda: None)
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


def test_w3c_action_chains_is_resolved_once_and_cached(monkeypatch):
    """The import machinery is not per-call work, and the answer cannot change mid-run."""
    monkeypatch.setattr(gestures, "_w3c_action_chains", gestures._UNRESOLVED)
    probes = 0

    def counting_resolver():
        nonlocal probes
        probes += 1
        return FakeActionChains

    monkeypatch.setattr(gestures, "_resolve_w3c_action_chains", counting_resolver)

    assert gestures._load_w3c_action_chains() is FakeActionChains
    assert gestures._load_w3c_action_chains() is FakeActionChains
    assert gestures._load_w3c_action_chains() is FakeActionChains

    assert probes == 1, "the client is probed once, not once per double-tap"


def test_w3c_action_chains_resolver_prefers_appium_then_falls_back_to_selenium(monkeypatch):
    """Appium-Python-Client 6.0.0 ships no action_chains, so selenium is the live branch."""
    import builtins

    real_import = builtins.__import__
    attempted: list[str] = []

    def tracking_import(name, *args, **kwargs):
        if name.endswith("action_chains"):
            attempted.append(name)
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", tracking_import)

    assert gestures._resolve_w3c_action_chains() is None
    assert attempted == [
        "appium.webdriver.common.action_chains",
        "selenium.webdriver.common.action_chains",
    ]


def test_human_double_click_is_a_noop_without_driver_or_element(w3c_supported):
    driver = FakeDriver()
    HumanizedGestureExecutor(driver).human_double_click(None)
    HumanizedGestureExecutor(None).human_double_click(FakeElement())
    HumanizedGestureExecutor(None).human_double_click_at_point(540.0, 1200.0)

    assert driver.taps == []
    assert w3c_supported.batches == []
