#!/usr/bin/env python3
"""
scripts/run_live_test.py
========================
Executes a verification run against a live Virtual Device Session or physical device.

The run is one ``JobFeedPipeline`` pass — the same engine the Automation Worker's handlers
drive, reached through the entry point it publishes (issue #391). This script used to hand
itself to ``SmokeHarness`` and thereby walk a second, bespoke implementation of a job feed
that no device run ever exercised twice; two implementations of one feed drift, and this
was the one nobody tested on a device. Which card was chosen, whether its JD was usable,
what was screened and why: that is now answered in one place, and this runner reports it.

Two things stay here because the pipeline cannot answer them for itself — it assumes a
usable session and knows nothing about challenges:

* **App activation** belongs to whoever opened the session.
* **The auth gate.** ``TakeoverHandler`` proves the session before the run starts, so a
  captcha stops a verification run before it begins reading somebody's job list.

Usage:
  python3 scripts/run_live_test.py [--keyword agent] [--device emulator-5554]
  python3 scripts/run_live_test.py --no-search --no-filter
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from boss_agent.async_bridge import run_sync
from boss_agent.enums import AuthStatus, TargetAction
from boss_agent.feed_pipeline import FeedStreamConfig, JobFeedPipeline
from boss_agent.matching import JobMatchGreetingService, MatchGreetingResult
from boss_agent.memory import ResumeMemoryManager, StructuredCandidateProfile
from boss_agent.screening import CandidateScreener
from boss_agent.screening_policy import ScreeningPolicy
from boss_agent.search_entities import FilterConfig, SearchConfig
from boss_agent.searches import get_global_search_registry
from boss_agent.settings import load_settings
from boss_agent.workflows import TakeoverHandler
from droid_agent_core.driver import AppiumSession, DriverConfig

console = Console()

BOSS_PACKAGE = "com.hpbr.bosszhipin"


def list_saved_searches() -> None:
    """Print all available preconfigured saved searches."""
    reg = get_global_search_registry()
    searches = reg.list_all()

    table = Table(title="📋 Available Saved Searches & Filter Presets")
    table.add_column("Search ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="magenta")
    table.add_column("Keyword", style="green")
    table.add_column("Industries", style="yellow")
    table.add_column("Education / Salary / Exp", style="dim")

    for s in searches:
        industries_str = ", ".join(s.filter.industries) if s.filter.industries else "全部"
        other_filters = f"{s.filter.education or '不限'} | {s.filter.salary or '不限'} | {s.filter.experience or '不限'}"
        table.add_row(
            s.id,
            s.name,
            s.search.keyword or "(无)",
            industries_str,
            other_filters,
        )

    console.print(table)


async def _log(line: str) -> None:
    """Send the pipeline's run log to the console a person is watching.

    The pipeline reports every decision it makes through this sink — search entry, each
    screened card, why a card was turned down. It exists because this runner's reader is a
    human standing at a terminal, so those lines belong on the console rather than in a
    worker task log nobody opened. That visibility is the point of the script: it replaced
    the step prints the old procedural walk used to emit, and nothing was lost with them.
    """
    console.print(line)


def _activate_app(driver: Any, package_name: str = BOSS_PACKAGE) -> None:
    """Bring Boss 直聘 to the foreground before anything reads the screen.

    The pipeline starts from whatever page it is handed — it recovers to home and searches,
    but it has no opinion about which app is in front. Whoever opened the session is what
    decides that, which is this script. Failures are swallowed on purpose: a driver without
    ``activate_app`` (or an app already in front) is not a reason to abandon a verification
    run, and the run itself will fail loudly if the screen really is wrong.
    """
    if hasattr(driver, "activate_app"):
        try:
            driver.activate_app(package_name)
            time.sleep(1.0)
        except Exception:
            pass


def _load_candidate_profile(
    *,
    resume_file: str | None,
    force_refresh_memory: bool,
) -> StructuredCandidateProfile | None:
    """Load the candidate profile the run will screen against, or ``None`` if there is none.

    Loading is the expensive half of drafting a greeting: with ``--force-refresh-memory`` it
    regenerates the profile from the resume through the LLM. So it is called only on the
    greeting path — ``--no-greeting`` skips it rather than paying for an answer the run
    will not use (issue #391).
    """
    memory_manager = ResumeMemoryManager()
    try:
        profile = memory_manager.load_memory(
            force_refresh=force_refresh_memory,
            resume_file=resume_file,
        )
    except FileNotFoundError:
        console.print(
            "[dim]No candidate resume or memory profile configured. Greeting draft will be skipped.[/dim]"
        )
        return None
    except Exception as e:
        # A verification run must survive an unreadable profile: the feed pass is what it
        # is here to verify, and a missing greeting does not invalidate an extraction.
        console.print(f"[yellow]⚠️  Failed to pre-load candidate memory upfront: {e}[/yellow]")
        return None

    if profile:
        console.print(
            f"👤 [bold green]Candidate Memory Profile Active:[/bold green] "
            f"[bold cyan]{profile.name}[/bold cyan] "
            f"({profile.years_of_experience}年经验, "
            f"核心技能: {', '.join(profile.core_skills[:3])})"
        )
    return profile


def build_feed_config(
    *,
    search_config: SearchConfig,
    filter_config: FilterConfig,
    candidate_profile: StructuredCandidateProfile | None,
    enable_greeting_draft: bool,
) -> FeedStreamConfig:
    """Translate what the operator asked for into the one declarative run the engine reads.

    ``FeedStreamConfig`` is the pipeline's whole vocabulary: it states an intent, and the
    engine decides which card that means. This is the same translation issue #390 wrote
    inside ``SmokeHarness`` — it lives here as well because that adapter keeps taking a
    ``SavedSearch`` by id and resolving the preset's own screening policy through
    ``resolve_screening_policy``, whereas this runner resolves the preset itself and hands
    over concrete configs. Two callers, one shape, so the fields mean the same thing in
    both.

    The screening policy is the one field deliberately left at the engine's own default.
    Resolving a preset's stored policy would call ``ScreeningPolicy.load_default()``, i.e.
    read the operator's global screening configuration, and change which cards a live
    verification run accepts. That is a behavioural change to a runner whose job is to
    report what the engine does, not to re-decide what the engine should accept.
    """
    should_search = search_config.should_search
    return FeedStreamConfig(
        # A verification run drafts a greeting; it never sends one. ``send_greeting=False``
        # is the depth the dispatch path reads: the screener still drafts, the record stays
        # re-sendable, and nothing is typed into an employer's chat window — a smoke test
        # has no business messaging a real recruiter.
        target_action=(TargetAction.AUTO_APPLY if enable_greeting_draft else TargetAction.SAVE_JD),
        send_greeting=False,
        # ``should_search`` is the whole search decision, keyword included: a config with no
        # keyword states ``enable_search=False`` so the pipeline resets to the home feed and
        # browses recommendations, which is what this runner always did with a keyword-less
        # config — reset to home, then do not search.
        keyword=search_config.keyword if should_search else None,
        enable_search=should_search,
        # This run verifies one posting. The old flow opened the top card and stopped
        # there; ``max_jobs=1`` says the same thing to the scanner.
        max_jobs=1,
        # The filter config carries its own enable flag and its own "nothing to apply", so
        # handing it over unchanged gets both halves of the old filter step: apply what is
        # configured, clear the dialog when nothing is.
        filter_config=filter_config,
        screening_policy=ScreeningPolicy(),
        candidate_profile=candidate_profile,
    )


def run_live_test(
    search_id: str | None = "default_agent_search",
    keyword: str | None = None,
    filter_config: FilterConfig | None = None,
    device_udid: str = "emulator-5554",
    server_url: str = "http://127.0.0.1:4723",
    resume_file: str | None = None,
    force_refresh_memory: bool = False,
    enable_greeting_draft: bool = True,
) -> bool:
    """Open a device session, run one feed pass, and report what it extracted.

    Returns ``True`` only when the run produced a posting. Silence is not a pass: the
    pipeline reports what it *kept*, so a run that extracted nothing — every card filtered
    out, or no readable detail page — verified nothing, and reporting success for a screen
    it never read is the one failure mode a verification script must not have.
    """
    reg = get_global_search_registry()
    if search_id:
        try:
            saved_search = reg.get(search_id)
            search_config = (
                SearchConfig(keyword=keyword) if keyword is not None else saved_search.search
            )
            active_filter = filter_config or saved_search.filter
            console.print(
                f"\n[bold cyan]🚀 Starting feed verification using Saved Search:[/bold cyan] [bold yellow]'{search_id}'[/bold yellow] ({saved_search.name})"
            )
        except KeyError as e:
            console.print(f"[bold red]❌ {e}[/bold red]")
            return False
    else:
        search_config = SearchConfig(keyword=keyword)
        active_filter = filter_config or FilterConfig()
        console.print(
            "\n[bold cyan]🚀 Starting feed verification on Virtual Device Session...[/bold cyan]"
        )

    if search_config.should_search:
        console.print(
            f"🔎 [bold magenta]Target Search Keyword:[/bold magenta] [yellow]'{search_config.keyword}'[/yellow]"
        )
    else:
        console.print("[dim]Search disabled: proceeding on default recommendation list.[/dim]")

    if active_filter.has_industry_filters:
        console.print(
            f"🏢 [bold magenta]Active Industries (多选):[/bold magenta] [yellow]{', '.join(active_filter.industries)}[/yellow]"
        )

    if active_filter.has_filters:
        console.print(
            f"🎯 [bold magenta]Active Filters:[/bold magenta] "
            f"学历: [yellow]{active_filter.education}[/yellow] | "
            f"薪资: [yellow]{active_filter.salary}[/yellow] | "
            f"经验: [yellow]{active_filter.experience}[/yellow] | "
            f"活跃: [yellow]{active_filter.activity}[/yellow] | "
            f"规模: [yellow]{','.join(active_filter.company_scales)}[/yellow]"
        )
    else:
        console.print("[dim]Filters disabled: proceeding without filtering.[/dim]")

    # Target device configuration
    config = DriverConfig(
        server_url=server_url,
        platform_name="Android",
        automation_name="UiAutomator2",
        device_name=device_udid,
        app_package=BOSS_PACKAGE,
        app_activity="com.hpbr.bosszhipin.module.launcher.WelcomeActivity",
        no_reset=True,
        auto_grant_permissions=True,
        new_command_timeout=300,
        extra_capabilities={
            "appium:udid": device_udid,
            "appium:uiautomator2ServerInstallTimeout": 60000,
            "appium:adbExecTimeout": 60000,
            "appium:ensureWebviewsHavePages": True,
            "appium:nativeWebScreenshot": True,
        },
    )

    session = AppiumSession(config)
    output_dir = Path.home() / ".boss_agent" / "artifacts"
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        console.print(f"[dim]Connecting to Appium server at {server_url}...[/dim]")
        driver = session.start()
        _activate_app(driver)
        console.print(
            "[bold green]✅ Connected to virtual device session and launched Boss 直聘![/bold green]"
        )

        time.sleep(1.0)

        # 1. Capture initial launch screenshot & page source
        screen_1_path = output_dir / "live_launch_screen.png"
        driver.save_screenshot(str(screen_1_path))
        page_source_path = output_dir / "live_page_source.xml"
        page_source_path.write_text(driver.page_source, encoding="utf-8")

        # 2. Prove the session is usable before anything reads the feed. The pipeline knows
        # nothing about captchas or login challenges, so this gate cannot move into it: a
        # challenge has to stop the run *here*, not after it has begun scrolling a login
        # wall (issue #391). It runs before the engine is even composed so a blocked run
        # costs an operator nothing but the wait for them to solve it.
        takeover = TakeoverHandler(driver, auto_confirm_for_test=False)
        auth_status = takeover.check_and_handle_takeover()
        if auth_status != AuthStatus.AUTHENTICATED:
            raise RuntimeError(f"Authentication failed: {auth_status}")

        # 3. Compose the run. The pipeline holds the feed pass end to end — search entry,
        # filter dialogs, recovery, viewport iteration, screening, extraction — with no
        # store, so this device run persists nothing and needs no broker behind it
        # (issue #389). The screener carries *this* run's matching service, so the greeting
        # is drafted by the object the operator configured rather than by a second one the
        # pipeline built for itself.
        matching_service = JobMatchGreetingService()
        candidate_profile = (
            _load_candidate_profile(
                resume_file=resume_file, force_refresh_memory=force_refresh_memory
            )
            if enable_greeting_draft
            else None
        )
        if candidate_profile:
            matching_service.set_candidate_profile(candidate_profile)

        pipeline = JobFeedPipeline(
            driver=driver,
            screener=CandidateScreener(matching_service=matching_service),
            log=_log,
        )
        feed_config = build_feed_config(
            search_config=search_config,
            filter_config=active_filter,
            candidate_profile=candidate_profile,
            enable_greeting_draft=enable_greeting_draft,
        )

        result = run_sync(pipeline.stream_jobs(feed_config))
        if not result.postings:
            # The pipeline hands back only what it kept: a card whose detail page yielded
            # nothing, or one the run withdrew at screening, is not a posting. On a device
            # "nothing extracted" is almost never a mystery — it is one screen's worth of
            # explanation, and the operator is the only one who can see that screen.
            raise RuntimeError(
                f"Verification run extracted no job posting "
                f"(outcome={result.outcome}, "
                f"reason={result.reason or 'n/a'}, "
                f"error={result.error_message or 'n/a'}, "
                f"scanned={result.scanned}, processed={result.processed}, "
                f"skipped={result.skipped})"
            )

        job = result.postings[0]
        console.print(
            f"\n📋 [bold green]Extracted Job Posting:[/bold green] {job.title} | {job.company_name} | {job.salary_range}"
        )

        if candidate_profile and result.greeting_message:
            # Rendering is presentation, and the pipeline does not present: the run reports
            # what it drafted, the operator reads it here. The match reasons are not part
            # of that report, but the pipeline logged them on the way through, so the same
            # reasoning appears one line earlier than the rendered card does.
            matching_service.render_match_card(
                job,
                MatchGreetingResult(
                    match_score=result.score,
                    match_reasons=[],
                    greeting_message=result.greeting_message,
                ),
            )

        # 4. Capture final screen
        final_screen = output_dir / "live_final_screen.png"
        driver.save_screenshot(str(final_screen))
        console.print(f"📸 Final screen captured: [cyan]{final_screen}[/cyan]")

        console.print(
            "\n[bold green]🎉 Feed verification PASSED on Virtual Device Session![/bold green]"
        )
        return True

    except Exception as e:
        console.print(f"\n[bold red]❌ Verification Run Error: {e}[/bold red]")
        import traceback

        traceback.print_exc()
        return False
    finally:
        session.stop()
        console.print("[dim]Virtual device session terminated cleanly.[/dim]")


def load_runner_settings(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load settings from configuration files with priority: local overrides -> configs -> defaults."""
    return load_settings(config_path=config_path)


def main():
    parser = argparse.ArgumentParser(
        description="Boss Agent Mobile feed verification on Virtual Device Session (Config-First)"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to custom settings configuration YAML/JSON file",
    )
    parser.add_argument(
        "--search-id",
        type=str,
        default=None,
        help="Saved search preset ID to execute (overrides config setting)",
    )
    parser.add_argument(
        "--list-searches",
        action="store_true",
        help="List all preconfigured saved search presets and exit",
    )
    parser.add_argument(
        "--keyword",
        type=str,
        default=None,
        help="Custom search keyword (overrides saved search preset keyword)",
    )
    parser.add_argument(
        "--no-search",
        action="store_true",
        default=None,
        help="Skip search and stay on default recommendation feed",
    )
    parser.add_argument(
        "--no-filter",
        action="store_true",
        default=None,
        help="Disable job filters",
    )
    parser.add_argument(
        "--resume",
        type=str,
        default=None,
        help="Path to candidate resume file (.pdf, .docx, .txt, .md) to initialize or refresh memory",
    )
    parser.add_argument(
        "--force-refresh-memory",
        action="store_true",
        default=None,
        help="Force re-generation of candidate memory profile from resume using LLM",
    )
    parser.add_argument(
        "--preview-timeout",
        type=float,
        default=None,
        help=(
            "Accepted for backward compatibility and ignored. It paced the pause before "
            "navigating back out of a greeting typed into a live chat box; a verification "
            "run drafts a greeting and sends nothing, so there is no pause left to time "
            "(issue #390). Passing it prints a notice rather than waiting silently."
        ),
    )
    parser.add_argument(
        "--no-greeting",
        action="store_true",
        default=None,
        help=(
            "Disable LLM match analysis and greeting draft generation: the candidate "
            "profile is not loaded and the run screens on its own"
        ),
    )
    parser.add_argument(
        "--device",
        type=str,
        default=None,
        help="Target ADB device UDID (overrides config setting)",
    )
    parser.add_argument(
        "--server-url",
        type=str,
        default=None,
        help="Appium server URL (overrides config setting)",
    )
    args = parser.parse_args()

    if args.list_searches:
        list_saved_searches()
        sys.exit(0)

    cfg = load_runner_settings(args.config)

    # Resolve settings: CLI flags take precedence over database SavedSearch
    search_id = args.search_id or "default_agent_search"
    saved_search = None
    if search_id:
        try:
            reg = get_global_search_registry()
            saved_search = reg.get(search_id)
        except Exception:
            saved_search = None

    enable_search = (
        False if args.no_search else (saved_search.enable_search if saved_search else True)
    )
    enable_filter = (
        False if args.no_filter else (saved_search.enable_filter if saved_search else True)
    )
    keyword = (
        args.keyword
        if args.keyword is not None
        else (saved_search.keyword if saved_search else None)
    )

    device_udid = args.device or cfg.get("device", "emulator-5554")
    server_url = args.server_url or cfg.get("server_url", "http://127.0.0.1:4723")

    resume_file = args.resume or cfg.get("resume_path")
    force_refresh = (
        True if args.force_refresh_memory else bool(cfg.get("force_refresh_memory", False))
    )
    enable_greeting = False if args.no_greeting else bool(cfg.get("enable_greeting", True))

    if args.preview_timeout is not None:
        # Say it out loud. The flag stayed because invocations are in muscle memory and in
        # runbooks, and an operator who typed it is entitled to know it now waits for
        # nothing — a silently accepted no-op would read as "the preview was too short".
        console.print(
            f"[yellow]⚠️  --preview-timeout ({args.preview_timeout}s) is inert:[/yellow] "
            f"[dim]a verification run types no greeting into a chat box, so there is no "
            f"preview pause to time (issue #390).[/dim]"
        )

    target_keyword = keyword if enable_search else None
    target_search_id = search_id if enable_search else None
    target_filter = (
        FilterConfig(
            education=None,
            salary=None,
            experience=None,
            activity=None,
            company_scales=[],
            industries=[],
            enable_filter=False,
        )
        if not enable_filter
        else None
    )

    success = run_live_test(
        search_id=target_search_id,
        keyword=target_keyword,
        filter_config=target_filter,
        device_udid=device_udid,
        server_url=server_url,
        resume_file=resume_file,
        force_refresh_memory=force_refresh,
        enable_greeting_draft=enable_greeting,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
