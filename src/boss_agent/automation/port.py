"""
boss_agent.automation.port
==========================
The platform-neutral driver/session contract the automation layers depend on.

Gestures, locators, interceptors and the page objects speak only this port. The one
platform adapter today (``appium_adapter``) is the sole module allowed to import Appium
or Selenium, so a future Web-platform worker can supply its own adapter without the
domain layer noticing.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol


class By(StrEnum):
    """Locator strategies a driver port must understand.

    The values are the W3C/Appium strategy names, so the Appium adapter can pass them
    through unchanged. Keeping the enum here — rather than in the adapter — is what lets
    ``locators`` describe a selector without importing a platform client.
    """

    ID = "id"
    XPATH = "xpath"
    ACCESSIBILITY_ID = "accessibility id"
    CLASS_NAME = "class name"
    ANDROID_UIAUTOMATOR = "-android uiautomator"
    NAME = "name"


class ElementHandle(Protocol):
    """One element located on the current screen."""

    rect: dict[str, int] | None
    text: str
    class_name: str

    def click(self) -> None: ...

    def send_keys(self, text: str) -> None: ...

    def clear(self) -> None: ...

    def find_elements(self, by: By | str, value: str) -> list[ElementHandle]: ...


class DriverPort(Protocol):
    """The capabilities the automation code actually uses, and nothing more."""

    def find_elements(self, by: By | str, value: str) -> list[ElementHandle]: ...

    def tap(self, points: Sequence[tuple[float, float]], duration: int = 0) -> None: ...

    def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration: int = 0,
    ) -> None: ...

    def double_tap(
        self,
        x: float,
        y: float,
        interval: float,
        duration_ms: tuple[int, int],
    ) -> bool:
        """Emit two taps ``interval`` seconds apart as one gesture; False when unsupported."""
        ...

    def activate_app(self, app_id: str) -> None: ...

    def press_keycode(self, keycode: int) -> None: ...

    def back(self) -> None: ...

    def get_window_size(self) -> dict[str, int]: ...

    @property
    def current_package(self) -> str | None: ...

    def quit(self) -> None: ...


__all__ = ["By", "DriverPort", "ElementHandle"]
