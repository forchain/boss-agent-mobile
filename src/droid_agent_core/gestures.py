"""
droid_agent_core.gestures
=========================
Humanized gesture synthesis, Bézier touch movements, and spatial jitter.
"""

import random
import time
from dataclasses import dataclass


@dataclass
class Point:
    x: float
    y: float


def calculate_bounding_box_jitter(
    left: float,
    top: float,
    width: float,
    height: float,
    jitter_factor: float = 0.25,
) -> tuple[float, float]:
    """Calculate a humanized randomized coordinate within an element's bounding box."""
    center_x = left + width / 2.0
    center_y = top + height / 2.0

    max_dx = (width / 2.0) * jitter_factor
    max_dy = (height / 2.0) * jitter_factor

    # Normal distribution centered around element midpoint
    offset_x = random.gauss(0, max(0.1, max_dx / 2.0))
    offset_y = random.gauss(0, max(0.1, max_dy / 2.0))

    # Clamp within bounding box
    final_x = max(left + 2, min(left + width - 2, center_x + offset_x))
    final_y = max(top + 2, min(top + height - 2, center_y + offset_y))

    return round(float(final_x), 1), round(float(final_y), 1)


class BézierTouchSynthesizer:
    """Generates cubic/quadratic Bézier curves to simulate human finger swipe gestures."""

    @staticmethod
    def generate_curve(
        start: Point,
        end: Point,
        steps: int = 20,
        deviation_ratio: float = 0.15,
    ) -> list[Point]:
        """Generate a series of interpolated Points following a curved human-like path."""
        dx = end.x - start.x
        dy = end.y - start.y

        # Generate control points with slight perpendicular deviation
        ctrl1_x = start.x + dx * 0.3 + (random.random() - 0.5) * dy * deviation_ratio
        ctrl1_y = start.y + dy * 0.3 + (random.random() - 0.5) * dx * deviation_ratio

        ctrl2_x = start.x + dx * 0.7 + (random.random() - 0.5) * dy * deviation_ratio
        ctrl2_y = start.y + dy * 0.7 + (random.random() - 0.5) * dx * deviation_ratio

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


class HumanizedGestureExecutor:
    """Applies humanized delays and gesture actions on an Appium/WebDriver instance."""

    def __init__(self, driver=None):
        self.driver = driver

    def random_sleep(self, min_sec: float = 1.0, max_sec: float = 2.5) -> None:
        """Pause execution with a human-like delay."""
        delay = random.uniform(min_sec, max_sec)
        time.sleep(delay)

    def human_click(self, element, jitter: bool = True) -> None:
        """Perform a humanized click on a mobile element."""
        if not self.driver or not element:
            return

        rect = getattr(element, "rect", None)
        if (
            jitter
            and isinstance(rect, dict)
            and all(
                k in rect and isinstance(rect[k], (int, float))
                for k in ("x", "y", "width", "height")
            )
        ):
            x, y = calculate_bounding_box_jitter(
                left=float(rect["x"]),
                top=float(rect["y"]),
                width=float(rect["width"]),
                height=float(rect["height"]),
            )
            if hasattr(self.driver, "tap"):
                self.driver.tap([(x, y)], duration=random.randint(60, 120))
            else:
                element.click()
        else:
            element.click()

        self.random_sleep(0.1, 0.3)

    def human_type(self, element, text: str, clear_first: bool = False) -> None:
        """Type text into an input element with realistic humanized timing."""
        if not element:
            return

        if clear_first and hasattr(element, "clear"):
            try:
                element.clear()
            except Exception:
                pass

        if hasattr(element, "send_keys"):
            for char in text:
                element.send_keys(char)
                time.sleep(random.uniform(0.03, 0.09))
        self.random_sleep(0.1, 0.3)
