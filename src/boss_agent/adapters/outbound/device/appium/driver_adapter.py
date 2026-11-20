"""
boss_agent.adapters.outbound.device.appium.driver_adapter
=========================================================
Wrap a raw Appium ``webdriver.Remote`` as the application's ``UIDriverPort``.

The W3C pointer batch that makes a double-tap land inside Android's detector window
is a capability of this adapter, not of the gesture use case.
"""

from __future__ import annotations

import contextlib
from typing import Any

#: Sentinel distinguishing "not resolved yet" from a resolved ``None``.
_UNRESOLVED = object()

# Resolved once at import, the way optional dependencies are resolved elsewhere in this
# repo: the import machinery is not per-call work, and the answer cannot change while the
# process runs. The selenium branch is the live one — Appium-Python-Client 6.0.0 ships no
# `appium.webdriver.common.action_chains` — but the appium probe is kept first.
_w3c_action_chains: Any = _UNRESOLVED


def _resolve_w3c_action_chains():
    """Probe the client for ``ActionChains``, appium's copy first, then selenium's."""
    with contextlib.suppress(ImportError):
        from appium.webdriver.common.action_chains import ActionChains

        return ActionChains
    with contextlib.suppress(ImportError):
        from selenium.webdriver.common.action_chains import ActionChains

        return ActionChains
    return None


def _load_w3c_action_chains():
    """Resolve the W3C ``ActionChains`` class once, or ``None`` when unavailable."""
    global _w3c_action_chains
    if _w3c_action_chains is _UNRESOLVED:
        _w3c_action_chains = _resolve_w3c_action_chains()
    return _w3c_action_chains


class AppiumDriverAdapter:
    """A ``UIDriverPort`` over a raw WebDriver; unknown attributes pass through."""

    def __init__(self, raw_driver: Any) -> None:
        self.raw_driver = raw_driver

    def __getattr__(self, name: str) -> Any:
        try:
            raw = object.__getattribute__(self, "raw_driver")
        except AttributeError:
            raise AttributeError(name) from None
        return getattr(raw, name)

    def find_elements(self, by: str, value: str) -> list[Any]:
        return self.raw_driver.find_elements(by=by, value=value)

    def tap(self, positions: list[tuple[float, float]], duration: int = 0) -> None:
        self.raw_driver.tap(positions, duration=duration)

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, duration: int = 0) -> None:
        self.raw_driver.swipe(start_x, start_y, end_x, end_y, duration=duration)

    def get_window_size(self) -> dict[str, int]:
        return self.raw_driver.get_window_size()

    def activate_app(self, app_id: str) -> bool | None:
        return self.raw_driver.activate_app(app_id)

    def press_keycode(self, keycode: int) -> None:
        self.raw_driver.press_keycode(keycode)

    def back(self) -> None:
        self.raw_driver.back()

    def perform_double_tap(self, x: float, y: float, interval: float) -> bool:
        """Emit both taps as one W3C pointer batch; ``False`` when unsupported."""
        action_chains_cls = _load_w3c_action_chains()
        if action_chains_cls is None:
            return False
        try:
            actions = action_chains_cls(self.raw_driver)
            (
                actions.w3c_actions.pointer_action.move_to_location(x, y)
                .pointer_down()
                .pointer_up()
                .pause(interval)
                .pointer_down()
                .pointer_up()
            )
            actions.perform()
        except Exception:
            return False
        return True
