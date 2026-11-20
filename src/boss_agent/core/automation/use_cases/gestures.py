"""
boss_agent.core.automation.use_cases.gestures
=============================================
Humanized gesture orchestration over an injected UI driver.

Timing, randomness, and the device driver are all constructor seams, so the whole
executor runs in memory. The W3C double-tap batch is a driver capability
(``perform_double_tap``) implemented by the Appium adapter; the fallbacks here are
the portable paths.
"""

from __future__ import annotations

import contextlib
import logging
import random
import time
from collections.abc import Callable

from ..domain.geometry import (
    TAP_DURATION_MS,
    BézierTouchSynthesizer,
    Point,
    RandomSource,
    calculate_bounding_box_jitter,
    clamp_double_tap_interval,
)
from ..interfaces.ui_driver import UIDriverPort, UIElementPort

ui_logger = logging.getLogger("droid_agent_core.ui")


def _describe_element(element: UIElementPort | None) -> str:
    """Compact, non-sensitive element description for UI debug telemetry."""
    if element is None:
        return "None"
    class_name = getattr(element, "class_name", None) or type(element).__name__
    rect = getattr(element, "rect", None)
    if isinstance(rect, dict):
        return f"{class_name} rect={{{rect.get('x')},{rect.get('y')},{rect.get('width')},{rect.get('height')}}}"
    return str(class_name)


class HumanizedGestureExecutor:
    """Applies humanized delays and gesture actions through a UI driver."""

    def __init__(
        self,
        driver: UIDriverPort | None = None,
        *,
        sleep: Callable[[float], None] | None = None,
        rng: RandomSource | None = None,
    ) -> None:
        self.driver = driver
        self._sleep = sleep
        self._rng: RandomSource = rng if rng is not None else random

    def _pause(self, seconds: float) -> None:
        (self._sleep or time.sleep)(seconds)

    def random_sleep(self, min_sec: float = 1.0, max_sec: float = 2.5) -> None:
        """Pause execution with a human-like delay."""
        self._pause(self._rng.uniform(min_sec, max_sec))

    def human_click(self, element: UIElementPort | None, jitter: bool = True) -> None:
        """Perform a humanized click on a mobile element."""
        if not self.driver or not element:
            return

        rect = getattr(element, "rect", None)
        if (
            jitter
            and isinstance(rect, dict)
            and all(
                k in rect and isinstance(rect[k], int | float)
                for k in ("x", "y", "width", "height")
            )
        ):
            x, y = calculate_bounding_box_jitter(
                left=float(rect["x"]),
                top=float(rect["y"]),
                width=float(rect["width"]),
                height=float(rect["height"]),
                rng=self._rng,
            )
            if hasattr(self.driver, "tap"):
                dur = self._rng.randint(60, 120)
                ui_logger.debug(
                    "[UI] click element=%s target=(%.1f, %.1f) duration=%dms via=driver.tap",
                    _describe_element(element),
                    x,
                    y,
                    dur,
                )
                self.driver.tap([(x, y)], duration=dur)
            else:
                ui_logger.debug(
                    "[UI] click element=%s target=(%.1f, %.1f) via=element.click",
                    _describe_element(element),
                    x,
                    y,
                )
                element.click()
        else:
            ui_logger.debug("[UI] click element=%s via=element.click", _describe_element(element))
            element.click()

        self.random_sleep(0.1, 0.3)

    def human_click_at_point(
        self,
        x: float,
        y: float,
        jitter_px: float = 3.0,
        duration_ms: tuple[int, int] = TAP_DURATION_MS,
    ) -> None:
        """Perform a humanized tap at an absolute screen coordinate with gaussian micro-jitter."""
        if not self.driver:
            return

        offset_x = self._rng.gauss(0, max(0.1, jitter_px / 2.0))
        offset_y = self._rng.gauss(0, max(0.1, jitter_px / 2.0))
        target_x = round(float(x + offset_x), 1)
        target_y = round(float(y + offset_y), 1)

        dur = self._rng.randint(duration_ms[0], duration_ms[1])
        ui_logger.debug(
            "[UI] tap target=(%.1f, %.1f) duration=%dms via=driver.tap",
            target_x,
            target_y,
            dur,
        )
        if hasattr(self.driver, "tap"):
            self.driver.tap([(target_x, target_y)], duration=dur)
        self.random_sleep(0.1, 0.3)

    def _double_click_element(self, element: UIElementPort, interval: float) -> None:
        """Two sequential ``element.click()`` calls, ``interval`` apart."""
        element.click()
        self._pause(interval)
        element.click()

    def _tap_twice_at(
        self, x: float, y: float, interval: float, duration_ms: tuple[int, int]
    ) -> None:
        """Two sequential ``driver.tap`` calls at one point, ``interval`` apart."""
        dur = self._rng.randint(duration_ms[0], duration_ms[1])
        self.driver.tap([(x, y)], duration=dur)
        self._pause(interval)
        self.driver.tap([(x, y)], duration=dur)

    def _double_tap_interval(self, interval_range: tuple[float, float]) -> float:
        return clamp_double_tap_interval(interval_range, rng=self._rng)

    def _emit_driver_double_tap(self, x: float, y: float, interval: float) -> bool:
        """Ask the driver for one pointer batch; ``False`` when it cannot."""
        perform = getattr(self.driver, "perform_double_tap", None)
        if not callable(perform):
            return False
        try:
            return perform(x, y, interval) is True
        except Exception as exc:
            ui_logger.debug("[UI] double_click W3C batch unavailable: %s", exc)
            return False

    def human_double_click(
        self,
        element: UIElementPort | None,
        interval_range: tuple[float, float] = (0.10, 0.22),
        jitter: bool = True,
        duration_ms: tuple[int, int] = TAP_DURATION_MS,
    ) -> None:
        """Perform a humanized double-tap on a mobile element.

        Both taps travel in a single pointer batch when the driver supports it, so the
        inter-tap gap is held by the device instead of the host.
        """
        if not self.driver or not element:
            return

        rect = getattr(element, "rect", None)
        interval = self._double_tap_interval(interval_range)

        if not (
            jitter
            and isinstance(rect, dict)
            and all(
                k in rect and isinstance(rect[k], int | float)
                for k in ("x", "y", "width", "height")
            )
        ):
            ui_logger.debug(
                "[UI] double_click element=%s interval=%dms via=element.click",
                _describe_element(element),
                int(interval * 1000),
            )
            self._double_click_element(element, interval)
            self.random_sleep(0.1, 0.3)
            return

        x, y = calculate_bounding_box_jitter(
            left=float(rect["x"]),
            top=float(rect["y"]),
            width=float(rect["width"]),
            height=float(rect["height"]),
            rng=self._rng,
        )

        if self._emit_driver_double_tap(x, y, interval):
            via = "w3c.pointer_actions"
        elif hasattr(self.driver, "tap"):
            self._tap_twice_at(x, y, interval, duration_ms)
            via = "driver.tap"
        else:
            self._double_click_element(element, interval)
            via = "element.click"

        ui_logger.debug(
            "[UI] double_click element=%s target=(%.1f, %.1f) interval=%dms via=%s",
            _describe_element(element),
            x,
            y,
            int(interval * 1000),
            via,
        )
        self.random_sleep(0.1, 0.3)

    def human_double_click_at_point(
        self,
        x: float,
        y: float,
        interval_range: tuple[float, float] = (0.10, 0.22),
        jitter_px: float = 3.0,
        duration_ms: tuple[int, int] = TAP_DURATION_MS,
    ) -> None:
        """Perform a humanized double-tap at an absolute coordinate with micro-jitter."""
        if not self.driver:
            return

        offset_x = self._rng.gauss(0, max(0.1, jitter_px / 2.0))
        offset_y = self._rng.gauss(0, max(0.1, jitter_px / 2.0))
        target_x = round(float(x + offset_x), 1)
        target_y = round(float(y + offset_y), 1)
        interval = self._double_tap_interval(interval_range)

        if self._emit_driver_double_tap(target_x, target_y, interval):
            via = "w3c.pointer_actions"
        elif hasattr(self.driver, "tap"):
            self._tap_twice_at(target_x, target_y, interval, duration_ms)
            via = "driver.tap"
        else:
            via = "unavailable"

        ui_logger.debug(
            "[UI] double_click target=(%.1f, %.1f) interval=%dms via=%s",
            target_x,
            target_y,
            int(interval * 1000),
            via,
        )
        self.random_sleep(0.1, 0.3)

    def human_type(
        self, element: UIElementPort | None, text: str, clear_first: bool = False
    ) -> None:
        """Type text into an input element with realistic humanized timing."""
        if not element or not text:
            return

        ui_logger.debug(
            "[UI] type element=%s len=%d clear_first=%s",
            _describe_element(element),
            len(text),
            clear_first,
        )

        if clear_first and hasattr(element, "clear"):
            with contextlib.suppress(Exception):
                element.clear()

        if hasattr(element, "send_keys"):
            element.send_keys(text)

        self.random_sleep(0.2, 0.5)

    def human_swipe(self, start: Point, end: Point, duration_ms: int = 500) -> None:
        """Perform a humanized swipe using Bézier interpolation or driver.swipe."""
        if not self.driver:
            return

        curve = BézierTouchSynthesizer.generate_curve(start, end, steps=10, rng=self._rng)
        start_pt = curve[0]
        end_pt = curve[-1]

        ui_logger.debug(
            "[UI] swipe (%.1f, %.1f) -> (%.1f, %.1f) duration=%dms",
            start_pt.x,
            start_pt.y,
            end_pt.x,
            end_pt.y,
            duration_ms,
        )
        if hasattr(self.driver, "swipe"):
            self.driver.swipe(
                int(start_pt.x),
                int(start_pt.y),
                int(end_pt.x),
                int(end_pt.y),
                duration=duration_ms,
            )
        self.random_sleep(0.3, 0.6)
