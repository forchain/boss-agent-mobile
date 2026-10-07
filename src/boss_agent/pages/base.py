"""
pages.base
==========
The one shared seam every Boss page object sits on (spec #395, ticket #398).

`BaseBossPage` is the only genuinely common code in the old monolith: driver lifecycle, key-based
locator resolution, and the hardware-Back affordance. The UI selector telemetry and the console
logging helpers live here too, because a page that could not be found and a page that failed must
still be reported through exactly the channels callers watch:

* `logger` stays named ``boss_agent.pages``. That string is a public observability contract -- it
  predates the package split, and log greps, dashboards, and string-targeted patches
  (`patch("boss_agent.pages.logger.error")`) all depend on it. Moving the definition here must not
  silently re-home the channel to ``boss_agent.pages.base``.
* `ui_logger` is the shared ``droid_agent_core.ui`` channel every selector query reports to.
* `console` is one `Console` instance, created once here and re-exported by the facade, so rendered
  output keeps its formatting no matter which submodule prints.
"""

import contextlib
import logging
import time
from typing import Any

from rich.console import Console

from droid_agent_core.gestures import HumanizedGestureExecutor
from droid_agent_core.locators import (
    LocatorRegistry,
    UISelector,
    get_global_locator_registry,
    wait_until,
)

from ..keyword_constants import PLATFORM_BADGE_MARKERS

logger = logging.getLogger("boss_agent.pages")
ui_logger = logging.getLogger("droid_agent_core.ui")
console = Console()


# Jittered hardware-Back cadence for search/home recovery. Deliberately a range,
# not a fixed interval: a perfectly regular Back rhythm is exactly the timing
# signature the humanized-interaction policy (ADR-0005) exists to avoid.
BACK_INTERVAL_SEC: tuple[float, float] = (0.35, 0.65)


def _log_selector_lookup(selector: UISelector, outcome: str, started_at: float) -> None:
    """Emit one UI telemetry line for a single selector query."""
    ui_logger.debug(
        "[UI] find key=%s by=%s selector=%.160s -> %s in %.2fs",
        selector.description or "-",
        selector.by.value,
        selector.value,
        outcome,
        time.monotonic() - started_at,
    )


def _log_info(msg: str) -> None:
    logger.info(msg)
    console.print(f"[cyan][JobDetailPage][/cyan] {msg}")


def _log_warn(msg: str) -> None:
    logger.warning(msg)
    console.print(f"[yellow][JobDetailPage ⚠️][/yellow] {msg}")


def _log_error(msg: str) -> None:
    logger.error(msg)
    console.print(f"[bold red][JobDetailPage ❌][/bold red] {msg}")


class BaseBossPage:
    """Base class for all Boss 直聘 Page Objects using key-based locator resolution."""

    BOSS_PACKAGE_NAME: str = "com.hpbr.bosszhipin"

    def __init__(self, driver: Any, locator_registry: LocatorRegistry | None = None):
        self.driver = driver
        self.gestures = HumanizedGestureExecutor(driver)
        self.locators = locator_registry or get_global_locator_registry()

    def activate_app(self, package_name: str = BOSS_PACKAGE_NAME) -> bool:
        """Ensure the Boss application is active and in foreground."""
        if hasattr(self.driver, "activate_app"):
            try:
                self.driver.activate_app(package_name)
                return True
            except Exception:
                pass
        return False

    def _get_window_size(self) -> dict[str, int]:
        """Get the current screen window dimensions with safe default fallback."""
        if hasattr(self.driver, "get_window_size"):
            try:
                size = self.driver.get_window_size()
                if isinstance(size, dict) and "width" in size and "height" in size:
                    w = int(size["width"])
                    h = int(size["height"])
                    if w > 100 and h > 100:
                        return {"width": w, "height": h}
            except Exception:
                pass
        return {"width": 1080, "height": 2400}

    def _find_by_selectors(self, selectors: list[UISelector]):
        if not self.driver or not selectors:
            return None
        for sel in selectors:
            started_at = time.monotonic()
            try:
                elems = self.driver.find_elements(by=sel.by.value, value=sel.value)
            except Exception as exc:
                _log_selector_lookup(sel, f"error:{exc}", started_at)
                continue
            _log_selector_lookup(sel, "match" if elems else "none", started_at)
            if elems:
                return elems[0]
        return None

    def _find_elements_by_key(
        self, key: str, format_args: dict[str, Any] | None = None
    ) -> list[Any]:
        """Return every element matched by the first locator for `key` that hits anything."""
        if not self.driver:
            return []
        for sel in self.locators.get_selectors(key, format_args=format_args):
            started_at = time.monotonic()
            try:
                elems = self.driver.find_elements(by=sel.by.value, value=sel.value)
            except Exception as exc:
                _log_selector_lookup(sel, f"error:{exc}", started_at)
                continue
            _log_selector_lookup(sel, "match" if elems else "none", started_at)
            if elems:
                return list(elems)
        return []

    def _extract_card_field_text(self, card_elem: Any, key: str) -> str:
        """Extract text from a sub-element inside a list card using configured selectors."""
        selectors = self.locators.get_selectors(key)
        for sel in selectors:
            try:
                if sel.by.value == "id":
                    elems = card_elem.find_elements(by="id", value=sel.value)
                elif sel.by.value == "xpath":
                    val = sel.value
                    if not val.startswith("."):
                        val = "." + val
                    elems = card_elem.find_elements(by="xpath", value=val)
                else:
                    elems = card_elem.find_elements(by=sel.by.value, value=sel.value)
                if elems:
                    for el in elems:
                        raw_t = getattr(el, "text", None)
                        if raw_t is not None:
                            txt = str(raw_t).strip()
                            if txt and txt not in PLATFORM_BADGE_MARKERS:
                                return txt
            except Exception:
                continue
        return ""

    def find_by_key(
        self,
        key: str,
        timeout_sec: float = 0.0,
        format_args: dict[str, Any] | None = None,
        default: str | list[str] | None = None,
    ):
        """Find an element using its configured key with automatic strategy detection."""
        ui_logger.debug("[UI] find_by_key '%s' timeout=%.1fs", key, timeout_sec)
        selectors = self.locators.get_selectors(key, format_args=format_args, default=default)
        if not selectors:
            ui_logger.debug("[UI] find_by_key '%s' -> no selectors configured", key)
            return None

        if timeout_sec > 0:
            try:
                return wait_until(
                    lambda: self._find_by_selectors(selectors),
                    timeout_sec=timeout_sec,
                    error_message=f"Element not found for key '{key}'",
                )
            except TimeoutError:
                ui_logger.debug("[UI] find_by_key '%s' -> timed out after %.1fs", key, timeout_sec)
                return None
        return self._find_by_selectors(selectors)

    def find_now(
        self,
        key: str,
        format_args: dict[str, Any] | None = None,
        default: str | list[str] | None = None,
    ):
        """Single fast-fail lookup: exactly one query, no polling.

        State checks that callers run inside their own retry loop use this, so a
        miss costs one selector query instead of a wait budget nobody honours.
        """
        return self.find_by_key(key, timeout_sec=0.0, format_args=format_args, default=default)

    def wait_for_key(
        self,
        key: str,
        timeout_sec: float = 10.0,
        format_args: dict[str, Any] | None = None,
        default: str | list[str] | None = None,
    ):
        """Wait until an element for the given key is found on screen."""
        ui_logger.debug("[UI] wait_for_key '%s' timeout=%.1fs", key, timeout_sec)
        if not self.driver:
            raise RuntimeError("Driver session is not initialized")
        selectors = self.locators.get_selectors(key, format_args=format_args, default=default)
        if not selectors:
            raise ValueError(f"No selector defined in configuration for key '{key}'")
        return wait_until(
            lambda: self._find_by_selectors(selectors),
            timeout_sec=timeout_sec,
            error_message=f"Timed out waiting for element key '{key}'",
        )

    def find_optional_element(self, selector: UISelector, timeout_sec: float = 0.0):
        """Backward compatible selector lookup."""
        if not self.driver:
            return None
        if timeout_sec > 0:
            try:
                return wait_until(
                    lambda: self._find_by_selectors([selector]),
                    timeout_sec=timeout_sec,
                    error_message=f"Element not found: {selector.description or selector.value}",
                )
            except TimeoutError:
                return None
        return self._find_by_selectors([selector])

    def wait_for_element(self, selector: UISelector, timeout_sec: float = 10.0):
        """Backward compatible selector wait."""
        if not self.driver:
            raise RuntimeError("Driver session is not initialized")
        return wait_until(
            lambda: self._find_by_selectors([selector]),
            timeout_sec=timeout_sec,
            error_message=f"Timed out waiting for element: {selector.description or selector.value}",
        )

    def press_back(self) -> None:
        """Send Android KEYCODE_BACK (keyevent 4) or driver.back() to press the hardware back button."""
        if not self.driver:
            return
        if hasattr(self.driver, "press_keycode"):
            try:
                ui_logger.debug("[UI] press KEYCODE_BACK (4) via=driver.press_keycode")
                self.driver.press_keycode(4)  # Android KEYCODE_BACK
                return
            except Exception:
                pass
        if hasattr(self.driver, "back"):
            ui_logger.debug("[UI] press back via=driver.back")
            with contextlib.suppress(Exception):
                self.driver.back()

    def _ensure_foreground(self) -> None:
        """Re-activate the Boss app if a Back press escaped it (e.g. to the launcher).

        The `current_package` property is itself a driver round-trip that can raise
        on a stale session, so a failure here must never abort the caller's loop.
        """
        if not self.driver:
            return
        try:
            package = getattr(self.driver, "current_package", None)
        except Exception:
            return
        if isinstance(package, str) and package and package != self.BOSS_PACKAGE_NAME:
            logger.warning("Foreground package is '%s' instead of Boss; re-activating app", package)
            self.activate_app()
