"""
boss_agent.adapters.memory.automation
=====================================
In-memory doubles for the automation ports: zero device, network, or disk I/O.
"""

from __future__ import annotations

import contextlib
from typing import Any

from boss_agent.core.automation.domain.locators import UISelector, parse_selector
from boss_agent.core.automation.interfaces.device_session import AdbCommandResult


class FakeUIElement:
    """One scripted element: records clicks/typing and can own child elements."""

    def __init__(
        self,
        rect: dict[str, Any] | None = None,
        class_name: str = "android.widget.TextView",
        text: str | None = None,
    ) -> None:
        self.rect = rect
        self.class_name = class_name
        self.text = text
        self.click_count = 0
        self.cleared = False
        self.typed_text = ""
        self.children: dict[tuple[str, str], list[FakeUIElement]] = {}

    def click(self) -> None:
        self.click_count += 1

    def clear(self) -> None:
        self.cleared = True
        self.typed_text = ""

    def send_keys(self, text: str) -> None:
        self.typed_text += text

    def find_elements(self, by: str, value: str) -> list[FakeUIElement]:
        return list(self.children.get((by, value), []))


class FakeUIDriver:
    """A scripted UI driver satisfying ``UIDriverPort``."""

    def __init__(
        self,
        *,
        window_size: dict[str, int] | None = None,
        double_tap_supported: bool = True,
    ) -> None:
        self.taps: list[tuple[list[tuple[float, float]], int]] = []
        self.swipes: list[tuple[int, int, int, int, int]] = []
        self.double_taps: list[tuple[float, float, float]] = []
        self.keycodes: list[int] = []
        self.back_presses = 0
        self.activated: list[str] = []
        self.find_calls: list[tuple[str, str]] = []
        self.elements: dict[tuple[str, str], list[FakeUIElement]] = {}
        self.window_size = window_size or {"width": 1080, "height": 2400}
        self.double_tap_supported = double_tap_supported

    def find_elements(self, by: str, value: str) -> list[FakeUIElement]:
        self.find_calls.append((by, value))
        return list(self.elements.get((by, value), []))

    def tap(self, positions: list[tuple[float, float]], duration: int = 0) -> None:
        self.taps.append((list(positions), duration))

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, duration: int = 0) -> None:
        self.swipes.append((start_x, start_y, end_x, end_y, duration))

    def get_window_size(self) -> dict[str, int]:
        return dict(self.window_size)

    def activate_app(self, app_id: str) -> bool:
        self.activated.append(app_id)
        return True

    def press_keycode(self, keycode: int) -> None:
        self.keycodes.append(keycode)

    def back(self) -> None:
        self.back_presses += 1

    def perform_double_tap(self, x: float, y: float, interval: float) -> bool:
        if not self.double_tap_supported:
            return False
        self.double_taps.append((x, y, interval))
        return True


class FakeDeviceSession:
    """A session that hands back a pre-built driver."""

    def __init__(self, driver: FakeUIDriver | None = None) -> None:
        self.driver = driver or FakeUIDriver()
        self.started = False
        self.stopped = False

    def start(self) -> FakeUIDriver:
        self.started = True
        return self.driver

    def activate_app(self, app_id: str | None = None) -> bool:
        return self.driver.activate_app(app_id or "")

    def stop(self) -> None:
        self.stopped = True

    def is_active(self) -> bool:
        return self.started and not self.stopped


class FakeAdbClient:
    """Records commands and replays scripted stdout."""

    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout
        self.commands: list[list[str]] = []

    def run(self, args: list[str]) -> AdbCommandResult:
        self.commands.append(list(args))
        return AdbCommandResult(stdout=self.stdout)

    def get_current_focus(self) -> str:
        return self.run(["shell", "dumpsys", "window"]).stdout.strip()


class InMemoryLocatorStore:
    """A ``LocatorStorePort`` over a plain dict, no files."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data: dict[str, Any] = dict(data or {})

    def reload(self) -> None:
        return None

    def get_raw(self, key: str, default: Any = None) -> Any:
        curr: Any = self._data
        for part in key.split("."):
            if isinstance(curr, dict) and part in curr:
                curr = curr[part]
            else:
                return default
        return curr

    def get_selectors(
        self,
        key: str,
        format_args: dict[str, Any] | None = None,
        default: str | list[str] | None = None,
    ) -> list[UISelector]:
        raw_val = self.get_raw(key, default)
        if raw_val is None:
            return []
        items = raw_val if isinstance(raw_val, list) else [raw_val]
        selectors: list[UISelector] = []
        for item in items:
            if isinstance(item, str) and format_args:
                with contextlib.suppress(Exception):
                    item = item.format(**format_args)
            selectors.append(parse_selector(item, description=key))
        return selectors
