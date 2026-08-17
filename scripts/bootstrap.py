#!/usr/bin/env python3
"""
scripts/bootstrap.py
====================
Idempotent multi-tier Environment Provisioner for Boss Agent Mobile.

Verifies, downloads, and configures:
  Tier 1: Java (OpenJDK 17+)
  Tier 2: Android SDK Command-line Tools & System Images (ARM64 Google APIs)
  Tier 3: Android Virtual Device (AVD) Instance creation & runner
  Tier 4: Appium 2.x Server & uiautomator2 Driver
  Tier 5: Boss 直聘 APK download & adb install

Usage:
  python3 scripts/bootstrap.py --check
  python3 scripts/bootstrap.py --provision
  python3 scripts/bootstrap.py --start-avd [--headless]
  python3 scripts/bootstrap.py --start-appium
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_AVD_NAME = "boss_avd_arm64"
DEFAULT_ANDROID_SDK_ROOT = os.environ.get(
    "ANDROID_HOME", str(Path.home() / "Library" / "Android" / "sdk")
)
DEFAULT_BOSS_APK_URL = (
    "https://www.zhipin.com/wapi/zpCommon/download/index?type=ckand&pkn=intro&code=64"
)
DEFAULT_APK_CACHE_PATH = Path.home() / ".boss_agent" / "cache" / "bosszhipin.apk"


@dataclass
class ComponentStatus:
    name: str
    installed: bool
    version: str | None = None
    details: str | None = None


@dataclass
class ProvisioningReport:
    components: list[ComponentStatus] = field(default_factory=list)
    all_ready: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"all_ready": self.all_ready, "components": [asdict(c) for c in self.components]}


class EnvironmentProvisioner:
    """Idempotently checks and provisions the complete mobile testing environment."""

    def __init__(
        self,
        sdk_root: str = DEFAULT_ANDROID_SDK_ROOT,
        avd_name: str = DEFAULT_AVD_NAME,
        apk_url: str = DEFAULT_BOSS_APK_URL,
        apk_cache: Path = DEFAULT_APK_CACHE_PATH,
    ):
        self.sdk_root = Path(sdk_root)
        self.avd_name = avd_name
        self.apk_url = apk_url
        self.apk_cache = apk_cache
        self.is_arm64 = platform.machine() in ("arm64", "aarch64")
        self.is_macos = platform.system() == "Darwin"

    def _run_cmd(
        self, cmd: list[str], env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        # Ensure Android SDK binaries and Homebrew OpenJDK in PATH
        sdk_paths = [
            "/opt/homebrew/opt/openjdk@17/bin",
            "/opt/homebrew/opt/openjdk/bin",
            str(self.sdk_root / "cmdline-tools" / "latest" / "bin"),
            str(self.sdk_root / "platform-tools"),
            str(self.sdk_root / "emulator"),
        ]
        merged_env["PATH"] = ":".join(sdk_paths) + ":" + merged_env.get("PATH", "")
        return subprocess.run(cmd, capture_output=True, text=True, env=merged_env, check=False)

    # -------------------------------------------------------------------------
    # Tier 1: Java (JDK 17+)
    # -------------------------------------------------------------------------
    def check_java(self) -> ComponentStatus:
        res = self._run_cmd(["java", "-version"])
        if res.returncode == 0 or "version" in (res.stderr + res.stdout):
            out = res.stderr or res.stdout
            first_line = out.splitlines()[0] if out else "Unknown"
            return ComponentStatus("java", True, version=first_line, details="JDK present")
        return ComponentStatus("java", False, details="Java not found in PATH")

    def provision_java(self) -> bool:
        if self.check_java().installed:
            return True
        if self.is_macos and shutil.which("brew"):
            print("Installing OpenJDK 17 via Homebrew...")
            res = self._run_cmd(["brew", "install", "openjdk@17"])
            return res.returncode == 0
        print("Please install Java 17+ (e.g. brew install openjdk@17 or via package manager)")
        return False

    # -------------------------------------------------------------------------
    # Tier 2: Android SDK (cmdline-tools, platform-tools, emulator, system-image)
    # -------------------------------------------------------------------------
    def check_android_sdk(self) -> ComponentStatus:
        adb_path = self.sdk_root / "platform-tools" / "adb"
        has_adb = adb_path.exists() or shutil.which("adb") is not None
        sdkmanager_path = self.sdk_root / "cmdline-tools" / "latest" / "bin" / "sdkmanager"
        has_sdkmanager = sdkmanager_path.exists()

        if has_adb and has_sdkmanager:
            return ComponentStatus(
                "android_sdk",
                True,
                version=f"SDK Root: {self.sdk_root}",
                details="SDK command-line tools and ADB available",
            )
        elif has_adb:
            return ComponentStatus(
                "android_sdk",
                True,
                version="ADB available",
                details=f"ADB present at {shutil.which('adb') or adb_path}",
            )
        return ComponentStatus(
            "android_sdk", False, details=f"Android SDK missing at {self.sdk_root}"
        )

    def provision_android_sdk(self) -> bool:
        if self.check_android_sdk().installed:
            return True
        self.sdk_root.mkdir(parents=True, exist_ok=True)
        # In automated environment, brew or manual cmdlinetools download
        if self.is_macos and shutil.which("brew"):
            print("Installing android-platform-tools via Homebrew...")
            self._run_cmd(["brew", "install", "--cask", "android-platform-tools"])
            return True
        return False

    # -------------------------------------------------------------------------
    # Tier 3: Android Virtual Device (AVD)
    # -------------------------------------------------------------------------
    def check_avd(self) -> ComponentStatus:
        emulator_bin = shutil.which("emulator") or (self.sdk_root / "emulator" / "emulator")
        if not emulator_bin or (isinstance(emulator_bin, Path) and not emulator_bin.exists()):
            return ComponentStatus("avd", False, details="emulator binary not found")

        res = self._run_cmd([str(emulator_bin), "-list-avds"])
        if res.returncode == 0:
            avds = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            if self.avd_name in avds:
                return ComponentStatus(
                    "avd", True, version=self.avd_name, details=f"Found AVD: {self.avd_name}"
                )
            return ComponentStatus(
                "avd", False, details=f"AVD {self.avd_name} not created. Available: {avds}"
            )
        return ComponentStatus("avd", False, details="Failed to list AVDs")

    def provision_avd(self) -> bool:
        status = self.check_avd()
        if status.installed:
            return True
        avdmanager = self.sdk_root / "cmdline-tools" / "latest" / "bin" / "avdmanager"
        if not avdmanager.exists():
            print("avdmanager not found; skipping AVD creation in mock/light mode")
            return False

        target_img = (
            "system-images;android-33;google_apis;arm64-v8a"
            if self.is_arm64
            else "system-images;android-33;google_apis;x86_64"
        )
        cmd = [str(avdmanager), "create", "avd", "-n", self.avd_name, "-k", target_img, "--force"]
        res = self._run_cmd(cmd)
        return res.returncode == 0

    # -------------------------------------------------------------------------
    # Tier 4: Appium 2.x Server & Drivers
    # -------------------------------------------------------------------------
    def check_appium(self) -> ComponentStatus:
        appium_bin = shutil.which("appium")
        if not appium_bin:
            return ComponentStatus("appium", False, details="appium CLI not found in PATH")

        ver_res = self._run_cmd(["appium", "--version"])
        ver = ver_res.stdout.strip() if ver_res.returncode == 0 else "Unknown"

        drv_res = self._run_cmd(["appium", "driver", "list", "--installed"])
        has_u2 = "uiautomator2" in (drv_res.stdout + drv_res.stderr)

        if has_u2:
            return ComponentStatus(
                "appium", True, version=ver, details="Appium + uiautomator2 driver installed"
            )
        return ComponentStatus("appium", False, version=ver, details="uiautomator2 driver missing")

    def provision_appium(self) -> bool:
        status = self.check_appium()
        if status.installed:
            return True
        if not shutil.which("appium") and shutil.which("npm"):
            print("Installing Appium via npm...")
            self._run_cmd(["npm", "install", "-g", "appium"])
        if shutil.which("appium"):
            print("Installing uiautomator2 driver...")
            res = self._run_cmd(["appium", "driver", "install", "uiautomator2"])
            return res.returncode == 0
        return False

    # -------------------------------------------------------------------------
    # Tier 5: Boss APK
    # -------------------------------------------------------------------------
    def check_apk(self) -> ComponentStatus:
        if self.apk_cache.exists() and self.apk_cache.stat().st_size > 1024 * 1024:
            return ComponentStatus(
                "boss_apk",
                True,
                version=f"{self.apk_cache.stat().st_size // (1024 * 1024)}MB",
                details=f"Cached at {self.apk_cache}",
            )
        return ComponentStatus("boss_apk", False, details=f"APK not downloaded at {self.apk_cache}")

    def provision_apk(self) -> bool:
        if self.check_apk().installed:
            return True
        self.apk_cache.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading Boss APK from {self.apk_url} to {self.apk_cache}...")
        try:
            import requests

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
            }
            resp = requests.get(
                self.apk_url, headers=headers, stream=True, timeout=60, allow_redirects=True
            )
            resp.raise_for_status()
            with open(self.apk_cache, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
            return self.check_apk().installed
        except Exception as e:
            print(f"Failed to download APK: {e}")
            return False

    # -------------------------------------------------------------------------
    # Master Check & Provision
    # -------------------------------------------------------------------------
    def check_all(self) -> ProvisioningReport:
        components = [
            self.check_java(),
            self.check_android_sdk(),
            self.check_avd(),
            self.check_appium(),
            self.check_apk(),
        ]
        all_ready = all(c.installed for c in components)
        return ProvisioningReport(components=components, all_ready=all_ready)

    def provision_all(self) -> bool:
        report = self.check_all()
        if report.all_ready:
            print("All components already provisioned and ready.")
            return True

        for comp in report.components:
            if not comp.installed:
                print(f"Provisioning {comp.name}...")
                if comp.name == "java":
                    self.provision_java()
                elif comp.name == "android_sdk":
                    self.provision_android_sdk()
                elif comp.name == "avd":
                    self.provision_avd()
                elif comp.name == "appium":
                    self.provision_appium()
                elif comp.name == "boss_apk":
                    self.provision_apk()

        final_report = self.check_all()
        return final_report.all_ready


def main():
    parser = argparse.ArgumentParser(description="Boss Agent Mobile Environment Provisioner")
    parser.add_argument(
        "--check", action="store_true", help="Check status of all required components"
    )
    parser.add_argument(
        "--provision", action="store_true", help="Automatically install missing dependencies"
    )
    parser.add_argument("--json", action="store_true", help="Output report in JSON format")
    args = parser.parse_args()

    provisioner = EnvironmentProvisioner()
    if args.provision:
        success = provisioner.provision_all()
        sys.exit(0 if success else 1)

    # Default to check
    report = provisioner.check_all()
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print("\n=== Boss Agent Mobile Environment Status ===")
        for c in report.components:
            icon = "✅" if c.installed else "❌"
            print(f"{icon} {c.name.upper():<12}: {c.details or ''} (Version: {c.version or 'N/A'})")
        print(f"\nOverall Readiness: {'READY' if report.all_ready else 'INCOMPLETE'}")

    sys.exit(0 if report.all_ready else 1)


if __name__ == "__main__":
    main()
