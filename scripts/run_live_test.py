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

What stays here is what is genuinely this script's: which saved search preset it resolves and
how, the device session it opens and closes, the two screenshots it captures for an operator
to look at afterwards, and the flags. The composition *around* the engine — the config
translation, the log sink, activation, the profile load, the refusal to report a run that
read nothing — is ``boss_agent.feed_verification``'s, because ``SmokeHarness`` composes the
same run and #391 left two copies of that block free to drift.

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

from boss_agent.config_realm import DEFAULTS
from boss_agent.enums import AuthStatus
from boss_agent.feed_pipeline import FeedStreamResult
from boss_agent.feed_verification import (
    BOSS_PACKAGE,
    activate_app,
    build_verification_pipeline,
    load_candidate_profile,
    render_greeting_match_card,
    run_verification_feed,
)
from boss_agent.matching import JobMatchGreetingService
from boss_agent.memory import ResumeMemoryManager
from boss_agent.screening import JobVerdictStage
from boss_agent.screening_policy import ScreeningPolicy
from boss_agent.search_entities import FilterConfig, SearchConfig
from boss_agent.searches import get_global_search_registry
from boss_agent.settings import load_settings
from boss_agent.workflows import TakeoverHandler
from droid_agent_core.driver import AppiumSession, DriverConfig

console = Console()


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


def _verdict_line(result: FeedStreamResult) -> str:
    """How the run's own outcome should be read out to the operator.

    "PASSED" is one word standing in for two different facts, and #391 printed the same line
    for both. A run that extracted a posting and had the screening engine *accept* it has
    verified the engine end to end. A run that extracted a posting and had the engine
    *reject* it has verified only that the app launches, searches, opens a card and parses a
    JD — which is most of what this script exists to check, but not the same claim, and an
    operator reading a green line had no way to tell them apart.

    Both are passes and neither is rewritten into a failure: making a correctly-rejected card
    fail the run would report the engine as broken every time it works. What changes is that
    the run says which of the two it was, so a screening rejection stops reading as a match.

    The rejected branch is a guard rather than a routine outcome today, and that is the
    runner's own doing: it hands the engine ``ScreeningPolicy()`` on purpose (see
    ``run_live_test``), and an empty blacklist means the deep screener always admits. It
    becomes reachable the moment that policy stops being empty, which is exactly why the
    wording has to be right before it matters.
    """
    outcome = str(result.outcome)
    if outcome == JobVerdictStage.FILTERED_BY_DEEP_SCREENER.value:
        return (
            "\n[bold yellow]✅ Feed verification PASSED (extraction verified, card screened out):[/bold yellow]\n"
            f"[dim]the run launched, searched, opened a card and parsed its JD; the screening "
            f"engine then turned that card down ({result.reason or outcome}). Extraction is "
            f"what this script verifies, and it succeeded.[/dim]"
        )
    return (
        "\n[bold green]🎉 Feed verification PASSED on Virtual Device Session![/bold green] "
        f"[dim](outcome={outcome}, score={result.score})[/dim]"
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
    pipeline hands back only the postings it kept, so an empty result means the run opened
    no card or had every card it opened withdrawn at the detail stage — it never read a job
    description, and reporting success for that is the one failure mode a verification script
    must not have. A card the *screening engine* rejected is not this failure: that run still
    proved extraction, and ``_verdict_line`` says so rather than reporting it as a match.
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
        activate_app(driver)
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
        # Loading the profile is the expensive half of drafting a greeting: with
        # ``--force-refresh-memory`` it regenerates the profile from the resume through the
        # LLM. So it happens only on the greeting path — ``--no-greeting`` skips it rather
        # than paying for an answer the run will not use (issue #391).
        candidate_profile = (
            load_candidate_profile(
                memory_manager=ResumeMemoryManager(),
                resume_file=resume_file,
                force_refresh_memory=force_refresh_memory,
                matching_service=matching_service,
            )
            if enable_greeting_draft
            else None
        )

        pipeline = build_verification_pipeline(driver, matching_service=matching_service)
        result = run_verification_feed(
            pipeline=pipeline,
            search_config=search_config,
            filter_config=active_filter,
            # Deliberately the engine's own default, not the preset's stored policy:
            # resolving it would call ``ScreeningPolicy.load_default()``, read the
            # operator's global screening configuration, and change which cards this live
            # run accepts. This script's job is to report what the engine does, not to
            # re-decide what the engine should accept (issue #391).
            screening_policy=ScreeningPolicy(),
            candidate_profile=candidate_profile,
            enable_greeting_draft=enable_greeting_draft,
        )

        job = result.postings[0]
        console.print(
            f"\n📋 [bold green]Extracted Job Posting:[/bold green] {job.title} | {job.company_name} | {job.salary_range}"
        )

        render_greeting_match_card(
            matching_service=matching_service,
            posting=job,
            result=result,
            candidate_profile=candidate_profile,
            enable_greeting_draft=enable_greeting_draft,
        )

        # 4. Capture final screen
        final_screen = output_dir / "live_final_screen.png"
        driver.save_screenshot(str(final_screen))
        console.print(f"📸 Final screen captured: [cyan]{final_screen}[/cyan]")

        console.print(_verdict_line(result))
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

    # Say it out loud, on both routes in. The flag stayed because invocations are in muscle
    # memory and in runbooks, and the key stayed because it is in DEFAULTS and therefore in
    # every operator's settings file — an operator who set `preview_timeout_sec: 10` and now
    # waits for nothing deserves the same notice as one who typed the flag. Only a *changed*
    # value is announced: DEFAULTS carries 3.0, so an untouched key would otherwise put the
    # warning in front of every run in the world (issue #391).
    configured_preview_timeout = cfg.get("preview_timeout_sec")
    if args.preview_timeout is not None:
        console.print(
            f"[yellow]⚠️  --preview-timeout ({args.preview_timeout}s) is inert:[/yellow] "
            f"[dim]a verification run types no greeting into a chat box, so there is no "
            f"preview pause to time (issue #390).[/dim]"
        )
    elif configured_preview_timeout is not None and float(configured_preview_timeout) != float(
        DEFAULTS["preview_timeout_sec"]
    ):
        console.print(
            f"[yellow]⚠️  preview_timeout_sec ({configured_preview_timeout}s) in your settings "
            f"is inert:[/yellow] "
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
