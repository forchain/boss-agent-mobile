"""
boss_agent.core.automation.interfaces.device_session
===================================================
Device session and ADB command ports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .ui_driver import UIDriverPort


@dataclass(frozen=True)
class AdbCommandResult:
    """The captured outcome of one ADB invocation."""

    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


class DeviceSessionPort(Protocol):
    """Lifecycle of one device automation session."""

    def start(self) -> UIDriverPort: ...

    def activate_app(self, app_id: str | None = None) -> bool: ...

    def stop(self) -> None: ...

    def is_active(self) -> bool: ...


class AdbCommandPort(Protocol):
    """The subset of ADB a run needs."""

    def run(self, args: list[str]) -> AdbCommandResult: ...

    def get_current_focus(self) -> str: ...
