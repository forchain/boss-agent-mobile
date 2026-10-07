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
from .candidate_entities import CandidateProfile
from .enums import AuthStatus
from .feed_verification import (
    activate_app,
    build_verification_pipeline,
    load_candidate_profile,
    render_greeting_match_card,
    run_verification_feed,
)
from .job_entities import JobPosting
from .matching import JobMatchGreetingService
from .memory import ResumeMemoryManager
from .pages import LoginPage
from .saved_search_store import (
    SavedSearchStore,
    missing_saved_search_message,
    resolve_saved_search_store,
)
from .screening_policy import ScreeningPolicy, resolve_screening_policy
from .search_entities import FilterConfig, SavedSearch, SearchConfig

console = Console()


def _require_saved_search(store: SavedSearchStore, search_id: str) -> SavedSearch:
    """Fetch one preset through ``store``, or name what is actually available.

    ``SmokeHarness`` is synchronous and every store verb is not, so the read is bridged
    with ``run_sync`` rather than ``asyncio.run`` — a harness built from inside a running
    loop (a LangGraph step, an async worker) would otherwise collide with it.

    A store answers a miss with ``None`` rather than raising, so this turns that
    ``None`` back into a ``KeyError`` listing the ids the store holds — the wording the
    CLI prints for the same miss, so neither caller has to be trusted over the other.
    Letting it through is not an option: the harness would fall through to a default
    search and filter and run an unfiltered sweep against a live device under a preset
    nobody chose.

    A store failure is not converted at all — it is raised as the typed ``BrokerError``
    the seam reports, because unlike a missing preset this says nothing about what the
    operator asked for and everything about whether the answer can be trusted.
    """
    loaded_search = run_sync(store.get_saved_search(search_id))
    if loaded_search is None:
        available = [search.id for search in run_sync(store.list_saved_searches())]
        raise KeyError(missing_saved_search_message(search_id, available))
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

    The composition *around* the engine — translating the options into one
    ``FeedStreamConfig``, wiring the run's log to a console, loading the profile, refusing
    to report a run that read nothing — is not here either. It lives in
    :mod:`boss_agent.feed_verification`, because ``scripts/run_live_test.py`` composes the
    same run and #391 left the two copies of it free to drift (issue #391).
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
        saved_search_store: SavedSearchStore | None = None,
    ):
        self.driver = driver
        self.takeover = takeover_handler or TakeoverHandler(driver, auto_confirm_for_test=True)

        self.resume_file = resume_file
        self.force_refresh_memory = force_refresh_memory
        self.enable_greeting_draft = enable_greeting_draft
        self.memory_manager = memory_manager or ResumeMemoryManager()
        self.matching_service = matching_service or JobMatchGreetingService()
        # Kept on the constructor because it is part of the adapter's published signature —
        # callers still pass it (``tests/unit/test_smoke_harness_greeting.py`` sets it to a
        # tenth of a second) and ticket #394 migrates this constructor against it, so
        # removing a parameter the seam is meant to carry forward is not this branch's call.
        # It no longer paces anything: the pause it was written for watched a greeting being
        # typed into a live chat box, and a verification run types nothing into one. The
        # comment it used to carry claimed ``scripts/run_live_test.py`` passed it; #391
        # dropped that argument, so the reason it names now is the only one left.
        self.preview_timeout_sec = (
            preview_timeout_sec
            if preview_timeout_sec is not None
            else float(self.memory_manager.candidate_config.get("preview_timeout_sec", 3.0))
        )

        # Pre-flight upfront candidate memory initialization. Loading is the expensive half of
        # drafting a greeting — with ``force_refresh_memory`` it regenerates the profile from
        # the resume through the LLM — so a greeting-less harness skips it rather than paying
        # for an answer the run will not use.
        self.candidate_profile: CandidateProfile | None = None
        if self.enable_greeting_draft:
            self.candidate_profile = load_candidate_profile(
                memory_manager=self.memory_manager,
                resume_file=self.resume_file,
                force_refresh_memory=self.force_refresh_memory,
                matching_service=self.matching_service,
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

        # One engine, wired for the interactive runner. The composition itself — pipeline,
        # screener, console log sink — is ``feed_verification``'s, because the CLI that an
        # operator runs on a device composes the same run and the two drifting apart is
        # what #390 and #391 existed to end (issue #391).
        self.pipeline = build_verification_pipeline(driver, matching_service=self.matching_service)

    def ensure_app_active(
        self, package_name: str = "com.hpbr.bosszhipin", timeout_sec: float = 5.0
    ) -> bool:
        """Ensure Boss 直聘 application is activated and brought to foreground."""
        return activate_app(self.driver, package_name)

    def run_smoke_test(self) -> JobPosting:
        """Run the feed once and return the posting it extracted.

        Thin on purpose: activate the app, prove the session is usable, delegate the run,
        return what came out of it. The auth gate is the one thing that cannot move into the
        pipeline — it knows nothing about challenges — so it stays here, in front of the run
        rather than behind it.
        """
        self.ensure_app_active()

        auth_status = self.takeover.check_and_handle_takeover()
        if auth_status != AuthStatus.AUTHENTICATED:
            raise RuntimeError(f"Authentication failed: {auth_status}")

        result = run_verification_feed(
            pipeline=self.pipeline,
            search_config=self.search_config,
            filter_config=self.filter_config,
            screening_policy=self.screening_policy,
            candidate_profile=self.candidate_profile,
            enable_greeting_draft=self.enable_greeting_draft,
        )
        render_greeting_match_card(
            matching_service=self.matching_service,
            posting=result.postings[0],
            result=result,
            candidate_profile=self.candidate_profile,
            enable_greeting_draft=self.enable_greeting_draft,
        )
        return result.postings[0]
