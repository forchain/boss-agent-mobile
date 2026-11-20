"""
boss_agent.core.automation.domain.geometry
==========================================
Humanized gesture geometry: spatial jitter, probe coordinates, and Bézier paths.

Pure mathematics over the standard library. Nothing here touches a device, a driver,
or the clock; execution lives in the application use cases and the Appium adapter.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Protocol

# Android's system double-tap detector pairs a second tap with the first only when
# it lands inside this window; past it the input stream is read as two single taps.
DOUBLE_TAP_MAX_INTERVAL = 0.30
# Headroom below the window, so a clamped gap stays strictly inside it.
_DOUBLE_TAP_INTERVAL_CAP = DOUBLE_TAP_MAX_INTERVAL - 0.03

#: Press duration range (ms) for a synthesized tap.
TAP_DURATION_MS = (60, 120)


class RandomSource(Protocol):
    """The subset of ``random.Random`` the geometry rules draw from."""

    def gauss(self, mu: float, sigma: float) -> float: ...

    def random(self) -> float: ...

    def randint(self, a: int, b: int) -> int: ...

    def uniform(self, a: float, b: float) -> float: ...


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class BoundingBox:
    """An element rectangle; ``from_rect`` accepts a driver's dict shape."""

    x: float
    y: float
    width: float
    height: float

    @classmethod
    def from_rect(cls, rect: dict[str, float | int]) -> BoundingBox:
        return cls(
            x=float(rect["x"]),
            y=float(rect["y"]),
            width=float(rect["width"]),
            height=float(rect["height"]),
        )


def calculate_bounding_box_jitter(
    left: float,
    top: float,
    width: float,
    height: float,
    jitter_factor: float = 0.25,
    rng: RandomSource = random,
) -> tuple[float, float]:
    """Calculate a humanized randomized coordinate within an element's bounding box."""
    center_x = left + width / 2.0
    center_y = top + height / 2.0

    max_dx = (width / 2.0) * jitter_factor
    max_dy = (height / 2.0) * jitter_factor

    offset_x = rng.gauss(0, max(0.1, max_dx / 2.0))
    offset_y = rng.gauss(0, max(0.1, max_dy / 2.0))

    final_x = max(left + 2, min(left + width - 2, center_x + offset_x))
    final_y = max(top + 2, min(top + height - 2, center_y + offset_y))

    return round(float(final_x), 1), round(float(final_y), 1)


def calculate_probe_coordinate(
    rect: dict[str, float | int],
    probe: tuple[float, float] | list[float],
    origin: str = "bottom-left",
) -> tuple[float, float]:
    """Calculate absolute screen coordinates for a relative probe point.

    Supported unit rules:
      - x <= 1.0: width ratio (0.0 to 1.0)
      - x > 1.0: pixels from origin X
      - y <= 1.0: height ratio (0.0 to 1.0)
      - y > 1.0: pixels from origin Y

    Origin convention (bottom-left):
      - X extends rightwards: left + dx
      - Y extends upwards into element: (top + height) - dy
    """
    left = float(rect["x"])
    top = float(rect["y"])
    width = float(rect["width"])
    height = float(rect["height"])

    probe_x, probe_y = float(probe[0]), float(probe[1])

    dx = probe_x * width if probe_x <= 1.0 else probe_x
    dy = probe_y * height if probe_y <= 1.0 else probe_y

    if origin == "bottom-left":
        target_x = left + dx
        target_y = (top + height) - dy
    elif origin == "top-left":
        target_x = left + dx
        target_y = top + dy
    elif origin == "bottom-right":
        target_x = (left + width) - dx
        target_y = (top + height) - dy
    else:
        target_x = left + dx
        target_y = (top + height) - dy

    clamped_x = max(left + 2, min(left + width - 2, target_x))
    clamped_y = max(top + 2, min(top + height - 2, target_y))

    return round(float(clamped_x), 1), round(float(clamped_y), 1)


def clamp_double_tap_interval(
    interval_range: tuple[float, float],
    rng: RandomSource = random,
) -> float:
    """Draw an inter-tap gap that always stays inside Android's double-tap window."""
    low = min(interval_range)
    high = min(max(interval_range), _DOUBLE_TAP_INTERVAL_CAP)
    low = min(low, high)
    return rng.uniform(low, high)


class BézierTouchSynthesizer:
    """Generates cubic Bézier curves to simulate human finger swipe gestures."""

    @staticmethod
    def generate_curve(
        start: Point,
        end: Point,
        steps: int = 20,
        deviation_ratio: float = 0.15,
        rng: RandomSource = random,
    ) -> list[Point]:
        """Generate interpolation points following a curved human-like path."""
        dx = end.x - start.x
        dy = end.y - start.y

        ctrl1_x = start.x + dx * 0.3 + (rng.random() - 0.5) * dy * deviation_ratio
        ctrl1_y = start.y + dy * 0.3 + (rng.random() - 0.5) * dx * deviation_ratio

        ctrl2_x = start.x + dx * 0.7 + (rng.random() - 0.5) * dy * deviation_ratio
        ctrl2_y = start.y + dy * 0.7 + (rng.random() - 0.5) * dx * deviation_ratio

        points: list[Point] = []
        for i in range(steps + 1):
            t = i / float(steps)
            inv_t = 1.0 - t
            x = (
                (inv_t**3) * start.x
                + 3 * (inv_t**2) * t * ctrl1_x
                + 3 * inv_t * (t**2) * ctrl2_x
                + (t**3) * end.x
            )
            y = (
                (inv_t**3) * start.y
                + 3 * (inv_t**2) * t * ctrl1_y
                + 3 * inv_t * (t**2) * ctrl2_y
                + (t**3) * end.y
            )
            points.append(Point(round(float(x), 1), round(float(y), 1)))

        return points
