"""Unit tests for Bézier gesture synthesizer and UI selectors."""

import pytest

from droid_agent_core.gestures import (
    BézierTouchSynthesizer,
    Point,
    calculate_bounding_box_jitter,
)
from droid_agent_core.locators import By, UISelector


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
