"""
boss_agent.workflows
====================
High-level automation workflows and safety takeover orchestrator.
"""

import time
from typing import Any

from rich.console import Console

from .models import AuthStatus, JobPosting
from .pages import JobDetailPage, JobListPage, LoginPage, StartupDialogPage

console = Console()


class TakeoverHandler:
    """Detects security challenges (captchas, SMS, login expired) and facilitates manual takeover."""

    def __init__(self, driver: Any, auto_confirm_for_test: bool = False):
        self.driver = driver
        self.login_page = LoginPage(driver)
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


class SmokeHarness:
    """Executes the Phase 1 End-to-End Smoke Test verifying app launch to job extraction."""

    def __init__(self, driver: Any, takeover_handler: TakeoverHandler | None = None):
        self.driver = driver
        self.startup_page = StartupDialogPage(driver)
        self.login_page = LoginPage(driver)
        self.list_page = JobListPage(driver)
        self.detail_page = JobDetailPage(driver)
        self.takeover = takeover_handler or TakeoverHandler(driver, auto_confirm_for_test=True)

    def run_smoke_test(self) -> JobPosting:
        """Run the full Phase 1 smoke harness flow."""
        # 1. Dismiss startup privacy dialogs if present
        if self.startup_page.is_dialog_present():
            self.startup_page.dismiss_dialog()

        # 2. Check auth / handle takeover
        auth_status = self.takeover.check_and_handle_takeover()
        if auth_status != AuthStatus.AUTHENTICATED:
            raise RuntimeError(f"Authentication failed: {auth_status}")

        # 3. Scroll job list
        self.list_page.scroll_job_list()

        # 4. Click top job
        clicked = self.list_page.select_first_job()
        if not clicked and self.driver:
            pass  # Continue to extraction attempt

        # 5. Extract job details
        posting = self.detail_page.extract_job_posting()

        # 6. Navigate back
        self.detail_page.navigate_back()

        return posting
