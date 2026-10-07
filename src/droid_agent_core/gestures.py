"""
droid_agent_core.gestures
=========================
Humanized gesture synthesis, Bézier touch movements, and spatial jitter.
"""

import contextlib
import logging
import random
import time
from dataclasses import dataclass

ui_logger = logging.getLogger("droid_agent_core.ui")

# Android's system double-tap detector pairs a second tap with the first only when
# it lands inside this window; past it the input stream is read as two single taps.
DOUBLE_TAP_MAX_INTERVAL = 0.30
# Headroom below the window, so a clamped gap stays strictly inside it.
_DOUBLE_TAP_INTERVAL_CAP = DOUBLE_TAP_MAX_INTERVAL - 0.03


def _load_w3c_action_chains():
    """Resolve the W3C ``ActionChains`` class, or ``None`` when the client lacks it."""
    with contextlib.suppress(ImportError):
        from appium.webdriver.common.action_chains import ActionChains

        return ActionChains
    with contextlib.suppress(ImportError):
        from selenium.webdriver.common.action_chains import ActionChains

        return ActionChains
    return None


def _describe_element(element) -> str:
    """Compact, non-sensitive element description for UI debug telemetry."""
    if element is None:
        return "None"
    class_name = getattr(element, "class_name", None) or type(element).__name__
    rect = getattr(element, "rect", None)
    if isinstance(rect, dict):
        return f"{class_name} rect={{{rect.get('x')},{rect.get('y')},{rect.get('width')},{rect.get('height')}}}"
    return str(class_name)


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

    # Clamp safely inside element boundary
    clamped_x = max(left + 2, min(left + width - 2, target_x))
    clamped_y = max(top + 2, min(top + height - 2, target_y))

    return round(float(clamped_x), 1), round(float(clamped_y), 1)


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
                k in rect and isinstance(rect[k], int | float)
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
                dur = random.randint(60, 120)
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
        duration_ms: tuple[int, int] = (60, 120),
    ) -> None:
        """Perform a humanized tap at an absolute screen coordinate with gaussian micro-jitter."""
        if not self.driver:
            return

        offset_x = random.gauss(0, max(0.1, jitter_px / 2.0))
        offset_y = random.gauss(0, max(0.1, jitter_px / 2.0))
        target_x = round(float(x + offset_x), 1)
        target_y = round(float(y + offset_y), 1)

        dur = random.randint(duration_ms[0], duration_ms[1])
        ui_logger.debug(
            "[UI] tap target=(%.1f, %.1f) duration=%dms via=driver.tap",
            target_x,
            target_y,
            dur,
        )
        if hasattr(self.driver, "tap"):
            self.driver.tap([(target_x, target_y)], duration=dur)
        self.random_sleep(0.1, 0.3)

    def _double_tap_interval(self, interval_range: tuple[float, float]) -> float:
        """Draw an inter-tap gap that always stays inside Android's double-tap window."""
        low = min(interval_range)
        high = min(max(interval_range), _DOUBLE_TAP_INTERVAL_CAP)
        low = min(low, high)
        return random.uniform(low, high)

    def _emit_w3c_double_tap(self, x: float, y: float, interval: float) -> bool:
        """Emit both taps as one W3C pointer batch; ``False`` when unsupported."""
        action_chains_cls = _load_w3c_action_chains()
        if action_chains_cls is None:
            return False
        try:
            actions = action_chains_cls(self.driver)
            (
                actions.w3c_actions.pointer_action.move_to_location(x, y)
                .pointer_down()
                .pointer_up()
                .pause(interval)
                .pointer_down()
                .pointer_up()
            )
            actions.perform()
        except Exception as exc:
            ui_logger.debug("[UI] double_click W3C batch unavailable: %s", exc)
            return False
        return True

    def human_double_click(
        self,
        element,
        interval_range: tuple[float, float] = (0.10, 0.22),
        jitter: bool = True,
    ) -> None:
        """Perform a humanized double-tap on a mobile element.

        Both taps travel in a single W3C pointer batch when the driver supports
        it, so the inter-tap gap is held by the device instead of the host: the
        round trip between two sequential `driver.tap` calls routinely pushes the
        second tap past Android's double-tap window, and the gesture then reads as
        two single taps.
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
            element.click()
            time.sleep(interval)
            element.click()
            self.random_sleep(0.1, 0.3)
            return

        x, y = calculate_bounding_box_jitter(
            left=float(rect["x"]),
            top=float(rect["y"]),
            width=float(rect["width"]),
            height=float(rect["height"]),
        )

        if self._emit_w3c_double_tap(x, y, interval):
            via = "w3c.pointer_actions"
        elif hasattr(self.driver, "tap"):
            dur = random.randint(60, 120)
            self.driver.tap([(x, y)], duration=dur)
            time.sleep(interval)
            self.driver.tap([(x, y)], duration=dur)
            via = "driver.tap"
        else:
            element.click()
            time.sleep(interval)
            element.click()
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
    ) -> None:
        """Perform a humanized double-tap at an absolute screen coordinate with gaussian micro-jitter."""
        if not self.driver:
            return

        offset_x = random.gauss(0, max(0.1, jitter_px / 2.0))
        offset_y = random.gauss(0, max(0.1, jitter_px / 2.0))
        target_x = round(float(x + offset_x), 1)
        target_y = round(float(y + offset_y), 1)
        interval = self._double_tap_interval(interval_range)

        if self._emit_w3c_double_tap(target_x, target_y, interval):
            via = "w3c.pointer_actions"
        elif hasattr(self.driver, "tap"):
            dur = random.randint(60, 120)
            self.driver.tap([(target_x, target_y)], duration=dur)
            time.sleep(interval)
            self.driver.tap([(target_x, target_y)], duration=dur)
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

    def human_type(self, element, text: str, clear_first: bool = False) -> None:
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

    def human_swipe(
        self,
        start: Point,
        end: Point,
        duration_ms: int = 500,
    ) -> None:
        """Perform a humanized vertical/horizontal swipe using Bézier interpolation or driver.swipe."""
        if not self.driver:
            return

        curve = BézierTouchSynthesizer.generate_curve(start, end, steps=10)
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
