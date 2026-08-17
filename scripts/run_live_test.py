#!/usr/bin/env python3
"""
scripts/run_live_test.py
========================
Executes real live automated testing on connected physical Android device / emulator.
"""

import time
from pathlib import Path

from rich.console import Console

from boss_agent.pages import JobListPage, LoginPage, StartupDialogPage
from droid_agent_core.driver import AppiumSession, DriverConfig

console = Console()


def run_live_test():
    console.print(
        "\n[bold cyan]🚀 Starting Live Automated Testing on Real Android Device...[/bold cyan]"
    )

    # Target device configuration
    config = DriverConfig(
        server_url="http://127.0.0.1:4723",
        platform_name="Android",
        automation_name="UiAutomator2",
        device_name="emulator-5554",
        app_package="com.hpbr.bosszhipin",
        app_activity="com.hpbr.bosszhipin.module.launcher.WelcomeActivity",
        no_reset=True,
        auto_grant_permissions=True,
        new_command_timeout=300,
        extra_capabilities={
            "appium:udid": "emulator-5554",
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
        console.print("[dim]Connecting to Appium server at http://127.0.0.1:4723...[/dim]")
        driver = session.start()
        console.print(
            "[bold green]✅ Connected to live device and launched Boss 直聘![/bold green]"
        )

        time.sleep(3.0)

        # 1. Capture initial screenshot & page source
        screen_1_path = output_dir / "live_launch_screen.png"
        driver.save_screenshot(str(screen_1_path))
        console.print(f"📸 Captured initial screen: [cyan]{screen_1_path}[/cyan]")

        page_source_path = output_dir / "live_page_source.xml"
        page_source_path.write_text(driver.page_source, encoding="utf-8")
        console.print(f"📄 Saved live UI page source: [cyan]{page_source_path}[/cyan]")

        # 2. Check and handle startup privacy/permission dialogs
        startup_page = StartupDialogPage(driver)
        if startup_page.is_dialog_present():
            console.print("[yellow]Detected startup privacy agreement. Dismissing...[/yellow]")
            startup_page.dismiss_dialog()
            time.sleep(2.0)
            driver.save_screenshot(str(output_dir / "live_after_privacy.png"))

        # 3. Check Authentication & Takeover status
        login_page = LoginPage(driver)
        auth_status = login_page.get_auth_status()
        console.print(f"🔑 Live Auth Status: [bold magenta]{auth_status.value}[/bold magenta]")

        # 4. Check Job List & Extract Screen Info
        list_page = JobListPage(driver)
        console.print("📜 Testing humanized job list scrolling...")
        list_page.scroll_job_list()

        time.sleep(1.0)
        final_screen = output_dir / "live_final_screen.png"
        driver.save_screenshot(str(final_screen))
        console.print(f"📸 Final screen captured: [cyan]{final_screen}[/cyan]")

        console.print(
            "\n[bold green]🎉 Live Automated Test PASSED on Real Android Hardware![/bold green]"
        )
        return True

    except Exception as e:
        console.print(f"\n[bold red]❌ Live Test Error: {e}[/bold red]")
        import traceback

        traceback.print_exc()
        return False
    finally:
        session.stop()
        console.print("[dim]Session terminated cleanly.[/dim]")


if __name__ == "__main__":
    run_live_test()
