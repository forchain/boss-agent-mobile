"""
droid_agent_core.locators
=========================
Declarative UI selectors, automatic locator strategy detection, and configuration management.
"""

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeVar

import yaml

T = TypeVar("T")


class By(StrEnum):
    ID = "id"
    XPATH = "xpath"
    ACCESSIBILITY_ID = "accessibility id"
    CLASS_NAME = "class name"
    ANDROID_UIAUTOMATOR = "-android uiautomator"
    NAME = "name"


@dataclass
class UISelector:
    by: By
    value: str
    description: str | None = None
    timeout: float = 5.0


def parse_selector(
    locator: str | dict[str, Any] | UISelector,
    description: str | None = None,
) -> UISelector:
    """Automatically deduce the Appium locator strategy (By) based on format and prefixes.

    Prefix rules:
      - 'xpath=...', '//...', '(//...', '//*...' -> By.XPATH
      - 'id=...', contains ':id/' -> By.ID
      - 'acc_id=...', 'accessibility_id=...', 'desc=...' -> By.ACCESSIBILITY_ID
      - 'class=...', 'class_name=...', 'android.widget...', 'androidx...' -> By.CLASS_NAME
      - 'uiautomator=...', 'new UiSelector()...' -> By.ANDROID_UIAUTOMATOR
      - 'text=...' -> By.XPATH (converted to //*[@text='...'])
    """
    if isinstance(locator, UISelector):
        return locator

    if isinstance(locator, dict):
        raw_val = locator.get("value") or locator.get("selector") or locator.get("locator") or ""
        desc = locator.get("desc") or locator.get("description") or description
        timeout = float(locator.get("timeout", 5.0))
        parsed = parse_selector(raw_val, description=desc)
        parsed.timeout = timeout
        return parsed

    raw = str(locator).strip()
    desc = description or raw

    # Explicit prefixes
    if raw.startswith("xpath="):
        return UISelector(By.XPATH, raw[6:].strip(), description=desc)
    if raw.startswith("id="):
        return UISelector(By.ID, raw[3:].strip(), description=desc)
    if raw.startswith("acc_id="):
        return UISelector(By.ACCESSIBILITY_ID, raw[7:].strip(), description=desc)
    if raw.startswith("accessibility_id="):
        return UISelector(By.ACCESSIBILITY_ID, raw[17:].strip(), description=desc)
    if raw.startswith("desc="):
        return UISelector(By.ACCESSIBILITY_ID, raw[5:].strip(), description=desc)
    if raw.startswith("class="):
        return UISelector(By.CLASS_NAME, raw[6:].strip(), description=desc)
    if raw.startswith("class_name="):
        return UISelector(By.CLASS_NAME, raw[11:].strip(), description=desc)
    if raw.startswith("uiautomator="):
        return UISelector(By.ANDROID_UIAUTOMATOR, raw[12:].strip(), description=desc)
    if raw.startswith("text="):
        text_val = raw[5:].strip()
        return UISelector(By.XPATH, f"//*[@text='{text_val}']", description=desc)

    # Implicit pattern detection
    if raw.startswith(("//", "(//", "//*")) or " | " in raw:
        return UISelector(By.XPATH, raw, description=desc)

    if raw.startswith("new UiSelector()"):
        return UISelector(By.ANDROID_UIAUTOMATOR, raw, description=desc)

    if ":id/" in raw:
        return UISelector(By.ID, raw, description=desc)

    if raw.startswith(("android.widget.", "androidx.", "android.view.")):
        return UISelector(By.CLASS_NAME, raw, description=desc)

    # Default fallback
    if "/" in raw or "[" in raw or "@" in raw:
        return UISelector(By.XPATH, raw, description=desc)

    return UISelector(By.ID, raw, description=desc)


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override dictionary into base dictionary."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge_dict(result[k], v)
        else:
            result[k] = v
    return result


class LocatorRegistry:
    """Loads and manages locator configurations with local override priority."""

    def __init__(
        self,
        base_config_path: str | Path | None = None,
        local_config_path: str | Path | None = None,
    ):
        self._data: dict[str, Any] = {}
        self.base_path = self._resolve_path(
            base_config_path,
            os.environ.get("LOCATORS_CONFIG_PATH", "config/locators.yaml"),
        )
        self.local_path = self._resolve_path(
            local_config_path,
            os.environ.get("LOCATORS_LOCAL_CONFIG_PATH", "config/locators.local.yaml"),
        )
        self.reload()

    def _resolve_path(self, user_path: str | Path | None, default_path: str) -> Path | None:
        if user_path is not None:
            return Path(user_path)
        p = Path(default_path)
        if p.is_absolute() and p.exists():
            return p
        repo_root = Path(__file__).resolve().parent.parent.parent
        candidate = repo_root / default_path
        if candidate.exists() or not p.exists():
            return candidate
        return p

    def reload(self) -> None:
        """Reload configuration from disk, applying local override on top of base."""
        merged: dict[str, Any] = {}

        # 1. Load base configuration
        if self.base_path and self.base_path.exists():
            try:
                content = self.base_path.read_text(encoding="utf-8")
                base_dict = yaml.safe_load(content) or {}
                if isinstance(base_dict, dict):
                    merged = base_dict
            except Exception as e:
                print(f"[LocatorRegistry] Warning loading base config {self.base_path}: {e}")

        # 2. Load local override configuration (Higher priority, git-ignored)
        if self.local_path and self.local_path.exists():
            try:
                content = self.local_path.read_text(encoding="utf-8")
                local_dict = yaml.safe_load(content) or {}
                if isinstance(local_dict, dict):
                    merged = _deep_merge_dict(merged, local_dict)
            except Exception as e:
                print(f"[LocatorRegistry] Warning loading local config {self.local_path}: {e}")

        self._data = merged

    def get_raw(self, key: str, default: Any = None) -> Any:
        """Retrieve raw config node by dot-separated path (e.g. 'search.search_input')."""
        parts = key.split(".")
        curr = self._data
        for part in parts:
            if isinstance(curr, dict) and part in curr:
                curr = curr[part]
            else:
                return default
        return curr

    def get_selectors(
        self,
        key: str,
        format_args: dict[str, Any] | None = None,
        default: str | list[str] | None = None,
    ) -> list[UISelector]:
        """Resolve a locator key to a list of UISelectors with automatic strategy detection."""
        raw_val = self.get_raw(key, default)
        if raw_val is None:
            return []

        items = raw_val if isinstance(raw_val, list) else [raw_val]
        selectors: list[UISelector] = []

        for item in items:
            if isinstance(item, str) and format_args:
                try:
                    formatted_val = item.format(**format_args)
                except Exception:
                    formatted_val = item
                selectors.append(parse_selector(formatted_val, description=key))
            else:
                selectors.append(parse_selector(item, description=key))

        return selectors


_GLOBAL_REGISTRY: LocatorRegistry | None = None


def get_global_locator_registry() -> LocatorRegistry:
    """Get or instantiate the global LocatorRegistry instance."""
    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        _GLOBAL_REGISTRY = LocatorRegistry()
    return _GLOBAL_REGISTRY


def wait_until(
    condition: Callable[[], T],
    timeout_sec: float = 10.0,
    poll_interval: float = 0.4,
    error_message: str = "Timed out waiting for condition",
) -> T:
    """Poll a callable condition until it returns a truthy value or timeout expires."""
    start_time = time.time()
    last_exception: Exception | None = None

    while time.time() - start_time < timeout_sec:
        try:
            res = condition()
            if res:
                return res
        except Exception as e:
            last_exception = e
        time.sleep(poll_interval)

    msg = f"{error_message} after {timeout_sec:.1f}s"
    if last_exception:
        msg += f" (last error: {last_exception})"
    raise TimeoutError(msg)


@dataclass
class ViewDescriptor:
    name: str
    required_selectors: list[UISelector] = field(default_factory=list)
    optional_selectors: list[UISelector] = field(default_factory=list)

    def is_present(self, driver: Any) -> bool:
        """Check if all required selectors for this view exist on current screen."""
        if not driver:
            return False
        for sel in self.required_selectors:
            try:
                elements = driver.find_elements(by=sel.by.value, value=sel.value)
                if not elements:
                    return False
            except Exception:
                return False
        return True

    def wait_until_present(self, driver: Any, timeout_sec: float = 10.0) -> bool:
        """Wait until all required selectors for this view are present."""
        return bool(
            wait_until(
                lambda: self.is_present(driver),
                timeout_sec=timeout_sec,
                error_message=f"Timed out waiting for view '{self.name}' to be present",
            )
        )
