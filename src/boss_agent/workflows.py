"""
boss_agent.workflows
====================
High-level operational workflows for Boss 直聘 automation.
"""

import time
from typing import Any

from rich.console import Console

from .models import AuthStatus, FilterConfig, JobPosting, SearchConfig
from .pages import (
    FilterDialogPage,
    JobDetailPage,
    JobListPage,
    LoginPage,
    SearchPage,
    StartupDialogPage,
)

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
    """Executes the End-to-End Smoke Test verifying app launch, optional search, filtering, to job extraction."""

    def __init__(
        self,
        driver: Any,
        takeover_handler: TakeoverHandler | None = None,
        search_config: SearchConfig | None = None,
        filter_config: FilterConfig | None = None,
    ):
        self.driver = driver
        self.startup_page = StartupDialogPage(driver)
        self.login_page = LoginPage(driver)
        self.list_page = JobListPage(driver)
        self.search_page = SearchPage(driver)
        self.filter_dialog = FilterDialogPage(driver)
        self.detail_page = JobDetailPage(driver)
        self.takeover = takeover_handler or TakeoverHandler(driver, auto_confirm_for_test=True)
        self.search_config = search_config or SearchConfig()
        self.filter_config = filter_config or FilterConfig()

    def run_smoke_test(self) -> JobPosting:
        """Run the full smoke harness flow with robust synchronization and verification."""
        # 1. Dismiss startup privacy dialogs if present
        if self.startup_page.is_dialog_present():
            self.startup_page.dismiss_dialog()

        # 2. Check auth / handle takeover
        auth_status = self.takeover.check_and_handle_takeover()
        if auth_status != AuthStatus.AUTHENTICATED:
            raise RuntimeError(f"Authentication failed: {auth_status}")

        # 3. Optional Search: If configured, enter search flow
        if self.search_config.should_search:
            keyword = self.search_config.keyword
            console.print(
                f"🔍 [bold cyan]Executing job search with keyword:[/bold cyan] '{keyword}'..."
            )
            # Ensure on job tab
            self.list_page.ensure_job_tab()

            # Open search page if not already there
            if not self.search_page.is_search_page() and not self.list_page.open_search(
                timeout_sec=10.0
            ):
                raise RuntimeError("Failed to open search screen from job tab")

            # Wait for search page input to be ready
            if not self.search_page.wait_for_search_page(timeout_sec=10.0):
                raise RuntimeError("Timed out waiting for search input screen to become ready")

            # Execute search and submit
            if not self.search_page.search(keyword, timeout_sec=15.0):  # type: ignore[arg-type]
                raise RuntimeError(f"Failed to submit search for keyword: '{keyword}'")

            # Wait until search results job cards appear
            if not self.list_page.wait_for_jobs_loaded(timeout_sec=15.0):
                raise RuntimeError(f"Timed out waiting for search results to load for '{keyword}'")

        # 4. Optional Filter: If configured, apply job filters
        if self.filter_config.has_filters:
            console.print("🎯 [bold cyan]Applying configured job filters...[/bold cyan]")
            if not self.filter_dialog.apply_filters(self.filter_config, timeout_sec=10.0):
                raise RuntimeError("Failed to open or apply configured job filters")

            # Wait until filtered job list reloads
            if not self.list_page.wait_for_jobs_loaded(timeout_sec=15.0):
                raise RuntimeError("Timed out waiting for filtered job list to load")

        # 5. Scroll job list
        self.list_page.scroll_job_list()

        # 6. Click top job and wait for detail page
        if not self.list_page.select_first_job(timeout_sec=10.0):
            raise RuntimeError("Failed to select first job posting in list")

        # 7. Extract real job details from detail screen
        posting = self.detail_page.extract_job_posting(timeout_sec=10.0)

        # 8. Navigate back
        self.detail_page.navigate_back()

        return posting
