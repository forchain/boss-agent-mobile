"""
boss_agent.core.automation.interfaces.ui_driver
===============================================
The UI driver and element seams the automation use cases drive.
"""

from __future__ import annotations

from typing import Any, Protocol


class UIElementPort(Protocol):
    """One element found on screen."""

    @property
    def rect(self) -> dict[str, Any] | None: ...

    @property
    def class_name(self) -> str | None: ...

    @property
    def text(self) -> str | None: ...

    def click(self) -> None: ...

    def clear(self) -> None: ...

    def send_keys(self, text: str) -> None: ...

    def find_elements(self, by: str, value: str) -> list[UIElementPort]: ...


class UIDriverPort(Protocol):
    """The device surface the use cases act on."""

    def find_elements(self, by: str, value: str) -> list[UIElementPort]: ...

    def tap(self, positions: list[tuple[float, float]], duration: int = 0) -> None: ...

    def swipe(
        self, start_x: int, start_y: int, end_x: int, end_y: int, duration: int = 0
    ) -> None: ...

    def get_window_size(self) -> dict[str, int]: ...

    def activate_app(self, app_id: str) -> bool | None: ...

    def press_keycode(self, keycode: int) -> None: ...

    def back(self) -> None: ...

    def perform_double_tap(self, x: float, y: float, interval: float) -> bool: ...
