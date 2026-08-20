"""
boss_agent.workflows
====================
High-level operational workflows for Boss 直聘 automation.
"""

import time
from pathlib import Path
from typing import Any

from rich.console import Console

from .matching import JobMatchGreetingService
from .memory import ResumeMemoryManager
from .models import AuthStatus, FilterConfig, JobPosting, SavedSearch, SearchConfig
from .pages import (
    ChatPage,
    FilterDialogPage,
    IndustryFilterDialogPage,
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
        saved_search: SavedSearch | None = None,
        saved_search_id: str | None = None,
        resume_file: str | Path | None = None,
        force_refresh_memory: bool = False,
        preview_timeout_sec: float = 3.0,
        enable_greeting_draft: bool = True,
        memory_manager: ResumeMemoryManager | None = None,
        matching_service: JobMatchGreetingService | None = None,
    ):
        self.driver = driver
        self.startup_page = StartupDialogPage(driver)
        self.login_page = LoginPage(driver)
        self.list_page = JobListPage(driver)
        self.search_page = SearchPage(driver)
        self.filter_dialog = FilterDialogPage(driver)
        self.industry_filter_dialog = IndustryFilterDialogPage(driver)
        self.detail_page = JobDetailPage(driver)
        self.chat_page = ChatPage(driver)
        self.takeover = takeover_handler or TakeoverHandler(driver, auto_confirm_for_test=True)

        self.resume_file = resume_file
        self.force_refresh_memory = force_refresh_memory
        self.enable_greeting_draft = enable_greeting_draft
        self.memory_manager = memory_manager or ResumeMemoryManager()
        self.matching_service = matching_service or JobMatchGreetingService()
        self.preview_timeout_sec = (
            preview_timeout_sec
            if preview_timeout_sec is not None
            else float(self.memory_manager.candidate_config.get("preview_timeout_sec", 3.0))
        )

        if saved_search:
            self.search_config = saved_search.search
            self.filter_config = saved_search.filter
        elif saved_search_id:
            from .searches import get_global_search_registry

            reg = get_global_search_registry()
            loaded_search = reg.get(saved_search_id)
            self.search_config = loaded_search.search
            self.filter_config = loaded_search.filter
        else:
            self.search_config = search_config or SearchConfig()
            self.filter_config = filter_config or FilterConfig()

    def ensure_app_active(
        self, package_name: str = "com.hpbr.bosszhipin", timeout_sec: float = 5.0
    ) -> bool:
        """Ensure Boss 直聘 application is activated and brought to foreground."""
        if hasattr(self.driver, "activate_app"):
            try:
                self.driver.activate_app(package_name)
                time.sleep(1.0)
                return True
            except Exception:
                pass
        return False

    def run_smoke_test(self) -> JobPosting:
        """Run the full smoke harness flow with robust synchronization and verification."""
        # 0. Ensure Boss app is active and in foreground
        self.ensure_app_active()

        # 1. Dismiss startup privacy dialogs if present
        if self.startup_page.is_dialog_present():
            self.startup_page.dismiss_dialog()

        # 2. Check auth / handle takeover
        auth_status = self.takeover.check_and_handle_takeover()
        if auth_status != AuthStatus.AUTHENTICATED:
            raise RuntimeError(f"Authentication failed: {auth_status}")

        # 3. Ensure navigation is reset to home page before starting query
        console.print("🏠 [dim]Ensuring navigation is reset to Home Page...[/dim]")
        if not self.list_page.navigate_to_home():
            raise RuntimeError("Failed to navigate back to Home Page before query execution")

        # 4. Optional Search: If configured, enter search flow
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

        # 4. Optional Filters
        # 4.1 Industry Filter (Multi-select)
        if self.filter_config.has_industry_filters:
            console.print(
                f"🏢 [bold cyan]Applying industry filters:[/bold cyan] {self.filter_config.industries}..."
            )
            if not self.industry_filter_dialog.apply_industry_filters(
                self.filter_config.industries, timeout_sec=10.0
            ):
                raise RuntimeError("Failed to open or apply configured industry filters")

            # Wait until filtered job list reloads
            if not self.list_page.wait_for_jobs_loaded(timeout_sec=15.0):
                raise RuntimeError("Timed out waiting for industry-filtered job list to load")

        # 4.2 General Filters (Education, Salary, Experience, Activity, Company Scales)
        has_general_filters = any(
            [
                bool(self.filter_config.education and self.filter_config.education.strip()),
                bool(self.filter_config.salary and self.filter_config.salary.strip()),
                bool(self.filter_config.experience and self.filter_config.experience.strip()),
                bool(self.filter_config.activity and self.filter_config.activity.strip()),
                bool(self.filter_config.company_scales),
            ]
        )
        if has_general_filters:
            console.print("🎯 [bold cyan]Applying configured general job filters...[/bold cyan]")
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

        # 8. Optional Match Evaluation and Greeting Draft (Fill in Chat, Do NOT send)
        if self.enable_greeting_draft:
            try:
                profile = self.memory_manager.load_memory(
                    force_refresh=self.force_refresh_memory,
                    resume_file=self.resume_file,
                )
                console.print(
                    f"📊 [bold cyan]Evaluating job match for candidate:[/bold cyan] {profile.name}..."
                )
                match_result = self.matching_service.evaluate_and_draft_greeting(profile, posting)
                self.matching_service.render_match_card(posting, match_result)

                # Open chat dialog and type greeting
                console.print("💬 [bold cyan]Opening chat dialog to type greeting draft...[/bold cyan]")
                if self.detail_page.open_chat(timeout_sec=5.0):
                    typed = self.chat_page.type_greeting_message(
                        match_result.greeting_message, timeout_sec=5.0
                    )
                    if typed:
                        console.print(
                            f"⏳ [bold yellow]Greeting message entered in chat box. "
                            f"Pausing for {self.preview_timeout_sec}s preview (NOT SENT)...[/bold yellow]"
                        )
                        time.sleep(self.preview_timeout_sec)
                    # Navigate back from chat dialog to job detail screen
                    self.chat_page.navigate_back()
            except FileNotFoundError:
                console.print(
                    "[dim]No resume or memory profile found. Skipping greeting draft generation.[/dim]"
                )
            except Exception as e:
                console.print(f"[yellow]⚠️  Matching/Greeting draft skipped due to error:[/yellow] {e}")

        # 9. Navigate back to job list
        self.detail_page.navigate_back()

        return posting

