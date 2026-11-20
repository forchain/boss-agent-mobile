"""
boss_agent.automation.locators
==============================
Declarative UI selectors, automatic locator strategy detection, and configuration management.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from .port import By, DriverPort

T = TypeVar("T")


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
    """Automatically deduce the driver locator strategy (By) based on format and prefixes.

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


def wait_until(
    condition: Callable[[], T],
    timeout_sec: float = 10.0,
    poll_interval: float = 0.4,
    error_message: str = "Timed out waiting for condition",
) -> T:
    """Poll a callable condition until it returns a truthy value or timeout expires.

    A blocking condition call cannot be preempted (one driver query can cost
    seconds), so the budget is checked *between* calls: once it is spent the loop
    exits without adding a trailing poll sleep, which would otherwise make a
    short timeout overshoot by a fixed amount.
    """
    start_time = time.time()
    last_exception: Exception | None = None
    max_retries = max(int(timeout_sec / max(poll_interval, 0.001)), 1)
    retries = 0

    while retries < max_retries:
        retries += 1
        try:
            res = condition()
            if res:
                return res
        except Exception as e:
            last_exception = e

        remaining = timeout_sec - (time.time() - start_time)
        if remaining <= 0:
            break
        time.sleep(min(poll_interval, remaining))

    msg = f"{error_message} after {timeout_sec:.1f}s"
    if last_exception:
        msg += f" (last error: {last_exception})"
    raise TimeoutError(msg)


@dataclass
class ViewDescriptor:
    name: str
    required_selectors: list[UISelector] = field(default_factory=list)
    optional_selectors: list[UISelector] = field(default_factory=list)

    def is_present(self, driver: DriverPort | None) -> bool:
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

    def wait_until_present(self, driver: DriverPort | None, timeout_sec: float = 10.0) -> bool:
        """Wait until all required selectors for this view are present."""
        return bool(
            wait_until(
                lambda: self.is_present(driver),
                timeout_sec=timeout_sec,
                error_message=f"Timed out waiting for view '{self.name}' to be present",
            )
        )
