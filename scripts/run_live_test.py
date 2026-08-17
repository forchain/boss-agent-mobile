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

from boss_agent.models import FilterConfig, SearchConfig
from boss_agent.workflows import SmokeHarness, TakeoverHandler
from droid_agent_core.driver import AppiumSession, DriverConfig

console = Console()


def run_live_test(
    keyword: str | None = "agent",
    filter_config: FilterConfig | None = None,
    device_udid: str = "emulator-5554",
    server_url: str = "http://127.0.0.1:4723",
) -> bool:
    search_config = SearchConfig(keyword=keyword)
    active_filter = filter_config or FilterConfig()

    console.print("\n[bold cyan]🚀 Starting Smoke Harness on Virtual Device Session...[/bold cyan]")
    if search_config.should_search:
        console.print(
            f"🔎 [bold magenta]Target Search Keyword:[/bold magenta] [yellow]'{search_config.keyword}'[/yellow]"
        )
    else:
        console.print("[dim]Search disabled: proceeding on default recommendation list.[/dim]")

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
        console.print(
            "[bold green]✅ Connected to virtual device session and launched Boss 直聘![/bold green]"
        )

        time.sleep(2.0)

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
        "--keyword",
        type=str,
        default="agent",
        help="Search keyword (default: 'agent')",
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

    target_keyword = None if args.no_search else args.keyword
    target_filter = (
        FilterConfig(
            education=None,
            salary=None,
            experience=None,
            activity=None,
            company_scales=[],
        )
        if args.no_filter
        else FilterConfig()
    )

    success = run_live_test(
        keyword=target_keyword,
        filter_config=target_filter,
        device_udid=args.device,
        server_url=args.server_url,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
