"""
boss_agent.adapters.outbound.device.appium.adb_client
====================================================
The ADB subprocess boundary, behind ``AdbCommandPort``.
"""

from __future__ import annotations

import subprocess

from boss_agent.core.automation.domain.errors import AdbCommandError
from boss_agent.core.automation.interfaces.device_session import AdbCommandResult


class SubprocessAdbClient:
    """Executes ``adb`` on the host, translating failures into a domain error."""

    def __init__(self, device_id: str | None = None) -> None:
        self.device_id = device_id

    def run(self, args: list[str]) -> AdbCommandResult:
        cmd = ["adb"]
        if self.device_id:
            cmd.extend(["-s", self.device_id])
        cmd.extend(args)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except OSError as e:
            raise AdbCommandError(f"adb {' '.join(args)} could not run: {e}") from e
        return AdbCommandResult(
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            returncode=result.returncode,
        )

    def get_current_focus(self) -> str:
        result = self.run(["shell", "dumpsys", "window", "|", "grep", "-E", "'mCurrentFocus'"])
        return result.stdout.strip()


#: Historical name.
AdbClient = SubprocessAdbClient
