#!/usr/bin/env python3
"""
scripts/run_live_test.py
========================
Executes the Smoke Harness against a live Virtual Device Session or physical device.

Usage:
  python3 scripts/run_live_test.py [--keyword agent] [--device emulator-5554]
  python3 scripts/run_live_test.py --no-search --no-filter
"""

import argparse
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table

from boss_agent.models import FilterConfig, SearchConfig
from boss_agent.searches import get_global_search_registry
from boss_agent.workflows import SmokeHarness, TakeoverHandler
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
        other_filters = (
            f"{s.filter.education or '不限'} | {s.filter.salary or '不限'} | {s.filter.experience or '不限'}"
        )
        table.add_row(
            s.id,
            s.name,
            s.search.keyword or "(无)",
            industries_str,
            other_filters,
        )

    console.print(table)


def run_live_test(
    search_id: str | None = "default_agent_search",
    keyword: str | None = None,
    filter_config: FilterConfig | None = None,
    device_udid: str = "emulator-5554",
    server_url: str = "http://127.0.0.1:4723",
) -> bool:
    reg = get_global_search_registry()
    if search_id:
        try:
            saved_search = reg.get(search_id)
            search_config = (
                SearchConfig(keyword=keyword) if keyword is not None else saved_search.search
            )
            active_filter = filter_config or saved_search.filter
            console.print(
                f"\n[bold cyan]🚀 Starting Smoke Harness using Saved Search:[/bold cyan] [bold yellow]'{search_id}'[/bold yellow] ({saved_search.name})"
            )
        except KeyError as e:
            console.print(f"[bold red]❌ {e}[/bold red]")
            return False
    else:
        search_config = SearchConfig(keyword=keyword)
        active_filter = filter_config or FilterConfig()
        console.print("\n[bold cyan]🚀 Starting Smoke Harness on Virtual Device Session...[/bold cyan]")

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
        app_package="com.hpbr.bosszhipin",
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
        if hasattr(driver, "activate_app"):
            try:
                driver.activate_app(config.app_package or "com.hpbr.bosszhipin")
                time.sleep(1.0)
            except Exception:
                pass
        console.print(
            "[bold green]✅ Connected to virtual device session and launched Boss 直聘![/bold green]"
        )

        time.sleep(1.0)

        # 1. Capture initial launch screenshot & page source
        screen_1_path = output_dir / "live_launch_screen.png"
        driver.save_screenshot(str(screen_1_path))
        page_source_path = output_dir / "live_page_source.xml"
        page_source_path.write_text(driver.page_source, encoding="utf-8")

        # 2. Run Smoke Harness
        takeover = TakeoverHandler(driver, auto_confirm_for_test=False)
        harness = SmokeHarness(
            driver=driver,
            takeover_handler=takeover,
            search_config=search_config,
            filter_config=active_filter,
        )
        job = harness.run_smoke_test()

        console.print(
            f"\n📋 [bold green]Extracted Job Posting:[/bold green] {job.title} | {job.company_name} | {job.salary_range}"
        )

        # 3. Capture final screen
        final_screen = output_dir / "live_final_screen.png"
        driver.save_screenshot(str(final_screen))
        console.print(f"📸 Final screen captured: [cyan]{final_screen}[/cyan]")

        console.print(
            "\n[bold green]🎉 Smoke Harness PASSED on Virtual Device Session![/bold green]"
        )
        return True

    except Exception as e:
        console.print(f"\n[bold red]❌ Smoke Harness Execution Error: {e}[/bold red]")
        import traceback

        traceback.print_exc()
        return False
    finally:
        session.stop()
        console.print("[dim]Virtual device session terminated cleanly.[/dim]")


def main():
    parser = argparse.ArgumentParser(
        description="Boss Agent Mobile Smoke Harness on Virtual Device Session"
    )
    parser.add_argument(
        "--search-id",
        type=str,
        default="default_agent_search",
        help="Saved search preset ID to execute (default: 'default_agent_search')",
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
        help="Skip search and stay on default recommendation feed",
    )
    parser.add_argument(
        "--no-filter",
        action="store_true",
        help="Disable job filters",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="emulator-5554",
        help="Target ADB device UDID (default: 'emulator-5554')",
    )
    parser.add_argument(
        "--server-url",
        type=str,
        default="http://127.0.0.1:4723",
        help="Appium server URL (default: 'http://127.0.0.1:4723')",
    )
    args = parser.parse_args()

    if args.list_searches:
        list_saved_searches()
        sys.exit(0)

    target_keyword = None if args.no_search else args.keyword
    target_filter = (
        FilterConfig(
            education=None,
            salary=None,
            experience=None,
            activity=None,
            company_scales=[],
            industries=[],
        )
        if args.no_filter
        else None
    )

    success = run_live_test(
        search_id=None if args.no_search else args.search_id,
        keyword=target_keyword,
        filter_config=target_filter,
        device_udid=args.device,
        server_url=args.server_url,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
