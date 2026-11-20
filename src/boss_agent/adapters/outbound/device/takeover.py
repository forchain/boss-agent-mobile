"""
boss_agent.adapters.outbound.device.takeover
============================================
Manual-takeover gate for a live device session.

``TakeoverHandler`` detects security challenges (captchas, SMS, login expired) and
facilitates manual takeover. It is a device-adapter concern: it reads the login screen
through the page objects and pauses for a human at the console.
"""

import time
from typing import Any

from rich.console import Console

from boss_agent.adapters.outbound.device.pages.system import LoginPage
from boss_agent.core.shared.enums import AuthStatus

console = Console()


class TakeoverHandler:
    """Detects security challenges (captchas, SMS, login expired) and facilitates manual takeover."""

    def __init__(self, driver: Any, auto_confirm_for_test: bool = False, locator_store: Any = None):
        self.driver = driver
        self.login_page = LoginPage(driver, locator_registry=locator_store)
        self.auto_confirm_for_test = auto_confirm_for_test

    def check_and_handle_takeover(self, timeout_sec: int = 300) -> AuthStatus:
        """Inspect auth status and pause for user intervention if challenge detected."""
        status = self.login_page.get_auth_status()
        if status == AuthStatus.AUTHENTICATED:
            return AuthStatus.AUTHENTICATED

        console.print(
            "\n[bold yellow]⚠️  [TAKEOVER REQUIRED][/bold yellow] "
            f"Detected status: [bold red]{status.value}[/bold red]."
        )
        console.print("Please complete the login/captcha on the Android Emulator GUI window.")

        if self.auto_confirm_for_test:
            console.print("[dim]Running in test mode: auto-confirmed.[/dim]")
            return AuthStatus.AUTHENTICATED

        start_time = time.time()
        while time.time() - start_time < timeout_sec:
            time.sleep(2.0)
            current_status = self.login_page.get_auth_status()
            if current_status == AuthStatus.AUTHENTICATED:
                console.print(
                    "[bold green]✅ Auth/Challenge resolved. Resuming automation...[/bold green]"
                )
                return AuthStatus.AUTHENTICATED

        console.print("[bold red]❌ Takeover timed out.[/bold red]")
        return status
