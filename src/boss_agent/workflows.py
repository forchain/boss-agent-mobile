"""
boss_agent.workflows
====================
High-level operational workflows for Boss 直聘 automation.
"""

import time
from pathlib import Path
from typing import Any

from rich.console import Console

from .async_bridge import run_sync
from .enums import AuthStatus
from .graph import run_job_application_graph
from .job_entities import JobCardBrief, JobPosting
from .matching import JobMatchGreetingService, MatchGreetingResult
from .memory import ResumeMemoryManager, StructuredCandidateProfile
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
from .saved_search_store import SavedSearchStore, resolve_saved_search_store
from .screening_policy import ScreeningPolicy, resolve_screening_policy
from .search_entities import FilterConfig, SavedSearch, SearchConfig

console = Console()


def _require_saved_search(store: SavedSearchStore, search_id: str) -> SavedSearch:
    """Fetch one preset through ``store``, or name what is actually available.

    ``SmokeHarness`` is synchronous and every store verb is not, so the read is bridged
    with ``run_sync`` rather than ``asyncio.run`` — a harness built from inside a running
    loop (a LangGraph step, an async worker) would otherwise collide with it.

    A store answers a miss with ``None`` where the registry raised, so this turns that
    ``None`` back into a ``KeyError`` listing the ids the store holds. Letting it through
    is not an option: the harness would fall through to a default search and filter and
    run an unfiltered sweep against a live device under a preset nobody chose.

    A store failure is not converted at all — it is raised as the typed ``BrokerError``
    the seam reports, because unlike a missing preset this says nothing about what the
    operator asked for and everything about whether the answer can be trusted.
    """
    loaded_search = run_sync(store.get_saved_search(search_id))
    if loaded_search is None:
        available = ", ".join(search.id for search in run_sync(store.list_saved_searches()))
        raise KeyError(
            f"Saved search '{search_id}' not found. Available searches: [{available or 'none'}]"
        )
    return loaded_search


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
        screening_policy: ScreeningPolicy | None = None,
        resume_file: str | Path | None = None,
        force_refresh_memory: bool = False,
        preview_timeout_sec: float = 3.0,
        enable_greeting_draft: bool = True,
        memory_manager: ResumeMemoryManager | None = None,
        matching_service: JobMatchGreetingService | None = None,
        saved_search_store: SavedSearchStore | None = None,
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

        # Pre-flight upfront candidate memory initialization
        self.candidate_profile: StructuredCandidateProfile | None = None
        if self.enable_greeting_draft:
            try:
                self.candidate_profile = self.memory_manager.load_memory(
                    force_refresh=self.force_refresh_memory,
                    resume_file=self.resume_file,
                )
                if self.candidate_profile:
                    self.matching_service.set_candidate_profile(self.candidate_profile)
                    console.print(
                        f"👤 [bold green]Candidate Memory Profile Active:[/bold green] "
                        f"[bold cyan]{self.candidate_profile.name}[/bold cyan] "
                        f"({self.candidate_profile.years_of_experience}年经验, "
                        f"核心技能: {', '.join(self.candidate_profile.core_skills[:3])})"
                    )
            except FileNotFoundError:
                console.print(
                    "[dim]No candidate resume or memory profile configured. Greeting draft will be skipped.[/dim]"
                )
            except Exception as e:
                console.print(
                    f"[yellow]⚠️  Failed to pre-load candidate memory upfront: {e}[/yellow]"
                )

        if saved_search:
            self.search_config = saved_search.search
            self.filter_config = saved_search.filter
            # A strategy's own recruitment channel overrides the preset's stored global
            # policy here too, through the same resolver the worker feed pipeline uses
            # (issue #368). Resolving it only on the task path would make one preset mean
            # two things: direct-only under a cron run, unrestricted under this runner.
            self.screening_policy = resolve_screening_policy(
                saved_search.screening_policy,
                channel_preference=saved_search.filter.channel_preference,
            )
        elif saved_search_id:
            # A named preset is resolved through the store seam, so the harness reaches
            # PocketBase with the broker's credentials instead of the registry's
            # unauthenticated side-channel read. Left to resolve its own store only
            # when a caller injects none, because that resolution builds an authenticated
            # database client the caller may not have wanted.
            store = saved_search_store or resolve_saved_search_store()
            loaded_search = _require_saved_search(store, saved_search_id)
            self.search_config = loaded_search.search
            self.filter_config = loaded_search.filter
            self.screening_policy = resolve_screening_policy(
                loaded_search.screening_policy,
                channel_preference=loaded_search.filter.channel_preference,
            )
        else:
            self.search_config = search_config or SearchConfig()
            self.filter_config = filter_config or FilterConfig()
            self.screening_policy = screening_policy or ScreeningPolicy()

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
        else:
            console.print(
                "🧹 [dim]No general filters configured; ensuring filters are cleared...[/dim]"
            )
            self.filter_dialog.clear_filters(timeout_sec=5.0)

        # 5. Scroll job list
        self.list_page.scroll_job_list()

        # 6. Click top job and wait for detail page
        if not self.list_page.select_first_job(timeout_sec=10.0):
            raise RuntimeError("Failed to select first job posting in list")

        # 7. Extract real job details from detail screen
        posting = self.detail_page.extract_job_posting(timeout_sec=10.0)

        # 8. Multi-Stage Screening and Greeting Draft via LangGraph
        if self.enable_greeting_draft and self.candidate_profile:
            try:
                console.print(
                    f"📊 [bold cyan]Evaluating job match via LangGraph for candidate:[/bold cyan] {self.candidate_profile.name}..."
                )
                card = JobCardBrief(
                    title=posting.title,
                    company_name=posting.company_name,
                    recruiter_name=posting.recruiter_name or "",
                    recruiter_title=posting.recruiter_title or "",
                    is_headhunter=posting.is_headhunter,
                    salary_range=posting.salary_range,
                    location=posting.location or "",
                    tags=posting.tags,
                )

                graph_state = run_job_application_graph(
                    card=card,
                    policy=self.screening_policy,
                    candidate_profile=self.candidate_profile,
                    jd_text=posting.job_description,
                    llm_client=getattr(self.matching_service, "llm_client", None),
                    matching_service=self.matching_service,
                )

                if not graph_state.get("keyword_pass", True):
                    console.print(
                        f"[yellow]⏭️  Job rejected by KeywordScreener: {graph_state.get('keyword_reason')}[/yellow]"
                    )
                elif not graph_state.get("app_rule_pass", True) and not graph_state.get(
                    "relaxed_by_whitelist", False
                ):
                    console.print(
                        f"[yellow]🛑  Job rejected by App-Enforced Filter: {graph_state.get('app_rule_violation')}[/yellow]"
                    )
                elif graph_state.get("status") == "jd_unavailable":
                    console.print(
                        f"[yellow]⚠️  No evaluable JD on this posting: "
                        f"{graph_state.get('error_message')}[/yellow]"
                    )
                elif not graph_state.get("deep_screen_pass", True):
                    console.print(
                        f"[yellow]⏭️  Job rejected by JDSemanticScreener: {graph_state.get('deep_screen_reason')}[/yellow]"
                    )
                else:
                    if graph_state.get("relaxed_by_whitelist"):
                        console.print(
                            f"🎗️  [bold cyan]Whitelist Relaxation rescue:[/bold cyan] "
                            f"{graph_state.get('relaxation_reason')}"
                        )
                    greeting_message = graph_state.get("greeting_message", "")
                    match_result = MatchGreetingResult(
                        match_score=graph_state.get("match_score", 80),
                        match_reasons=graph_state.get("match_reasons", []),
                        greeting_message=greeting_message,
                    )
                    self.matching_service.render_match_card(posting, match_result)

                    # Open chat dialog and type greeting
                    console.print(
                        "💬 [bold cyan]Opening chat dialog to type greeting draft...[/bold cyan]"
                    )
                    if self.detail_page.open_chat(timeout_sec=5.0):
                        typed = self.chat_page.type_greeting_message(
                            greeting_message, timeout_sec=5.0
                        )
                        if typed:
                            console.print(
                                f"⏳ [bold yellow]Greeting message entered in chat box. "
                                f"Pausing for {self.preview_timeout_sec}s preview (NOT SENT)...[/bold yellow]"
                            )
                            time.sleep(self.preview_timeout_sec)
                        # Navigate back from chat dialog to job detail screen
                        self.chat_page.navigate_back()
            except Exception as e:
                console.print(
                    f"[yellow]⚠️  Matching/Greeting draft skipped due to error:[/yellow] {e}"
                )

        # 9. Navigate back to job list
        self.detail_page.navigate_back()

        return posting
