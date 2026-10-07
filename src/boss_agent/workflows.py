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
from .enums import AuthStatus, TargetAction
from .feed_pipeline import FeedStreamConfig, JobFeedPipeline
from .job_entities import JobPosting
from .matching import JobMatchGreetingService, MatchGreetingResult
from .memory import ResumeMemoryManager, StructuredCandidateProfile
from .pages import LoginPage
from .screening import CandidateScreener
from .screening_policy import ScreeningPolicy, resolve_screening_policy
from .search_entities import FilterConfig, SavedSearch, SearchConfig

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
    """Runs one verified feed pass and hands back the posting it extracted.

    This used to be the second implementation of a job feed: it owned eight page objects
    and walked them itself — activate the app, dismiss the startup dialog, search, apply
    two filter dialogs, scroll, click the top card, parse the detail page — duplicating
    recovery, filtering, screening and extraction that ``JobFeedPipeline`` already maintains
    for the worker (issue #390). Two implementations of one feed is how they drift, and the
    runner is the one nobody tests on a device.

    So this is an adapter and nothing else. It translates what an operator typed at the CLI
    — a ``SearchConfig``, a ``FilterConfig``, a ``ScreeningPolicy``, a candidate profile —
    into one ``FeedStreamConfig``, hands the run to ``JobFeedPipeline``, and returns the
    ``JobPosting`` that run extracted. Which card was chosen, whether its JD was usable,
    what was screened and why: all of that is now answered in exactly one place.

    What stays here is the two things the pipeline cannot answer for itself. App activation
    belongs to whoever opened the session. And the auth challenge — the whole point of
    ``TakeoverHandler`` — is a precondition of touching the feed at all: the pipeline
    assumes a usable session, so the gate that proves one stays in front of it, where a
    captcha stops a verification run before it starts rather than after it has begun
    reading somebody's job list.
    """

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
    ):
        self.driver = driver
        self.takeover = takeover_handler or TakeoverHandler(driver, auto_confirm_for_test=True)

        self.resume_file = resume_file
        self.force_refresh_memory = force_refresh_memory
        self.enable_greeting_draft = enable_greeting_draft
        self.memory_manager = memory_manager or ResumeMemoryManager()
        self.matching_service = matching_service or JobMatchGreetingService()
        # Retained on the constructor because ``scripts/run_live_test.py`` passes it, and
        # because an operator still sets it. It no longer paces anything: the pause it was
        # written for watched a greeting being typed into a live chat box, and a smoke test
        # types nothing into one (see ``_feed_config``).
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
            from .searches import get_global_search_registry

            reg = get_global_search_registry()
            loaded_search = reg.get(saved_search_id)
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

        # One engine, wired for the interactive runner: the volatile ledger of #389 (this
        # harness has no broker behind it), the run's log going to the console a person is
        # watching, and a screener holding *this* run's matching service — so the greeting
        # is drafted by the service the caller injected and asserted on, rather than by a
        # second one the pipeline built for itself.
        self.pipeline = JobFeedPipeline(
            driver=driver,
            screener=CandidateScreener(matching_service=self.matching_service),
            log=self._log,
        )

    async def _log(self, line: str) -> None:
        """Print the pipeline's run log to the operator's console.

        The pipeline reports every decision it makes — search entry, each screened card,
        the greeting it drafted, why a card was turned down — through this sink. This
        runner is the sink: it exists so a person can watch a run, so those lines belong
        on the console where the old procedural prints were, not in a worker task log
        nobody opened.
        """
        console.print(line)

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

    def _feed_config(self) -> FeedStreamConfig:
        """Translate the operator's options into the one feed run that will execute."""
        should_search = self.search_config.should_search
        return FeedStreamConfig(
            # A smoke test drafts a greeting; it never sends one. ``send_greeting=False``
            # is the depth the pipeline reads for that: the screener still drafts, the
            # record still lands re-sendable, and nothing is typed into the employer's
            # chat window — a verification run has no business messaging a real recruiter.
            target_action=(
                TargetAction.AUTO_APPLY if self.enable_greeting_draft else TargetAction.SAVE_JD
            ),
            send_greeting=False,
            # ``should_search`` is the whole search decision, keyword included: a run with
            # no keyword states ``enable_search=False`` so the pipeline resets to the home
            # feed and browses recommendations, which is what this harness always did with
            # a keyword-less config — reset to home, then do not search.
            keyword=self.search_config.keyword if should_search else None,
            enable_search=should_search,
            # The smoke test verifies one posting. The old flow opened the top card and
            # stopped there; ``max_jobs=1`` says the same thing to the scanner.
            max_jobs=1,
            # The filter config carries its own enable flag and its own "nothing to
            # apply", so handing it over unchanged gets both halves of the old step 4:
            # apply what is configured, clear the dialog when nothing is.
            filter_config=self.filter_config,
            screening_policy=self.screening_policy,
            candidate_profile=self.candidate_profile,
        )

    def run_smoke_test(self) -> JobPosting:
        """Run the feed once and return the posting it extracted.

        ``stream_jobs`` is the single entry point for feed discovery and extraction, so
        this method is deliberately thin: activate the app, prove the session is usable,
        delegate the run, return what came out of it. The auth gate is the one thing that
        cannot move into the pipeline — it knows nothing about challenges — so it stays
        here, in front of the run rather than behind it.
        """
        self.ensure_app_active()

        auth_status = self.takeover.check_and_handle_takeover()
        if auth_status != AuthStatus.AUTHENTICATED:
            raise RuntimeError(f"Authentication failed: {auth_status}")

        result = run_sync(self.pipeline.stream_jobs(self._feed_config()))
        if not result.postings:
            # The pipeline hands back only what it kept: a card whose detail page yielded
            # nothing, or one the run withdrew at screening, is not a posting. The old
            # runner returned the first card regardless of what screening then made of it,
            # so it always had something to return; a run that extracted nothing now has
            # nothing, and a verification run that verified nothing has failed — silently
            # returning it would report success for a screen it never read. The outcome
            # and reason are in the message because on a device "nothing extracted" is
            # almost never a mystery: it is one screen's worth of explanation, which the
            # caller is the only one who can see.
            raise RuntimeError(
                f"Smoke test extracted no job posting "
                f"(outcome={result.outcome}, "
                f"reason={result.reason or 'n/a'}, "
                f"error={result.error_message or 'n/a'}, "
                f"scanned={result.scanned}, processed={result.processed}, "
                f"skipped={result.skipped})"
            )

        if self.enable_greeting_draft and self.candidate_profile and result.greeting_message:
            # Rendering is presentation and the pipeline does not present — the run
            # reports what it drafted, the operator reads it here. The match reasons are
            # not part of that report (they are not persisted and `FeedStreamResult` does
            # not carry them), but the pipeline logged them on the way through, so the
            # operator sees the same reasoning one line earlier than before.
            self.matching_service.render_match_card(
                result.postings[0],
                MatchGreetingResult(
                    match_score=result.score,
                    match_reasons=[],
                    greeting_message=result.greeting_message,
                ),
            )
        return result.postings[0]
