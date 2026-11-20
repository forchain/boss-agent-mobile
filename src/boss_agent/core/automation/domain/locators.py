"""
boss_agent.core.automation.domain.locators
==========================================
Declarative UI selectors and automatic strategy detection.

Pure parsing rules over the standard library. Loading a locator file and querying a
driver live in the adapters; a ``ViewDescriptor`` here is data only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class By(StrEnum):
    ID = "id"
    XPATH = "xpath"
    ACCESSIBILITY_ID = "accessibility id"
    CLASS_NAME = "class name"
    ANDROID_UIAUTOMATOR = "-android uiautomator"
    NAME = "name"


LocatorStrategy = By


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
    """Automatically deduce the locator strategy (By) based on format and prefixes.

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

    if raw.startswith(("//", "(//", "//*")) or " | " in raw:
        return UISelector(By.XPATH, raw, description=desc)

    if raw.startswith("new UiSelector()"):
        return UISelector(By.ANDROID_UIAUTOMATOR, raw, description=desc)

    if ":id/" in raw:
        return UISelector(By.ID, raw, description=desc)

    if raw.startswith(("android.widget.", "androidx.", "android.view.")):
        return UISelector(By.CLASS_NAME, raw, description=desc)

    if "/" in raw or "[" in raw or "@" in raw:
        return UISelector(By.XPATH, raw, description=desc)

    return UISelector(By.ID, raw, description=desc)


def deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into ``base``."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge_dict(result[k], v)
        else:
            result[k] = v
    return result


#: Backward-compatible private spelling.
_deep_merge_dict = deep_merge_dict


@dataclass(frozen=True)
class ViewDescriptor:
    """A named screen signature: all required selectors must be present."""

    name: str
    required_selectors: list[UISelector] = field(default_factory=list)
    optional_selectors: list[UISelector] = field(default_factory=list)
