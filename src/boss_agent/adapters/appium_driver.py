"""
boss_agent.adapters.appium_driver
=================================
The one module allowed to import Appium/Selenium.

Everything else in ``boss_agent`` speaks :class:`~boss_agent.automation.port.DriverPort`.
:class:`AppiumDriver` is the thin translation layer: an Appium ``Remote`` behind the
port, with the W3C pointer batch (the only gesture the raw client cannot express)
implemented here rather than in the platform-neutral gesture engine. Elements come back
as :class:`_RemoteElement`, so callers never reach for an Appium ``WebElement`` directly.
"""

import contextlib
import logging
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from appium import webdriver
from appium.options.android import UiAutomator2Options

from boss_agent.automation.port import By, ElementHandle

ui_logger = logging.getLogger("boss_agent.automation.ui")

# Resolved once at import, the way optional dependencies are resolved elsewhere in
# this repo: the import machinery is not per-call work, and the answer cannot change
# while the process runs. The selenium branch is the live one — Appium-Python-Client
# 6.0.0 ships no `appium.webdriver.common.action_chains` — but the appium probe is
# kept first so a client that does provide it is preferred.
_unresolved = object()
_w3c_action_chains: Any = _unresolved


def _load_w3c_action_chains():
    """Resolve the W3C ``ActionChains`` class once, or ``None`` when the client lacks it."""
    global _w3c_action_chains
    if _w3c_action_chains is _unresolved:
        _w3c_action_chains = _resolve_w3c_action_chains()
    return _w3c_action_chains


def _resolve_w3c_action_chains():
    """Probe the client for ``ActionChains``, appium's copy first, then selenium's."""
    with contextlib.suppress(ImportError):
        from appium.webdriver.common.action_chains import ActionChains

        return ActionChains
    with contextlib.suppress(ImportError):
        from selenium.webdriver.common.action_chains import ActionChains

        return ActionChains
    return None


def _by_value(by: By | str) -> str:
    return by.value if isinstance(by, By) else by


def _wrap(element: Any) -> ElementHandle | None:
    """Translate a raw Appium element into the neutral handle, or pass ``None`` through."""
    return None if element is None else _RemoteElement(element)


class _RemoteElement:
    """An Appium ``WebElement`` exposed only through the neutral ``ElementHandle`` surface."""

    def __init__(self, element: Any):
        self._element = element

    @property
    def rect(self) -> dict[str, int] | None:
        return self._element.rect

    @property
    def text(self) -> str:
        return self._element.text

    @property
    def class_name(self) -> str:
        return self._element.class_name

    def click(self) -> None:
        self._element.click()

    def send_keys(self, text: str) -> None:
        self._element.send_keys(text)

    def clear(self) -> None:
        self._element.clear()

    def get_attribute(self, name: str) -> str | None:
        return self._element.get_attribute(name)

    def find_element(self, by: By | str, value: str) -> ElementHandle | None:
        return _wrap(self._element.find_element(by=_by_value(by), value=value))

    def find_elements(self, by: By | str, value: str) -> list[ElementHandle]:
        return [
            handle
            for handle in (
                _wrap(element)
                for element in self._element.find_elements(by=_by_value(by), value=value)
            )
            if handle is not None
        ]


class AppiumDriver:
    """An Appium ``Remote`` exposed through the platform-neutral driver port."""

    def __init__(self, remote: webdriver.Remote):
        self._remote = remote

    def find_elements(self, by: By | str, value: str) -> list[ElementHandle]:
        return [
            handle
            for handle in (
                _wrap(element)
                for element in self._remote.find_elements(by=_by_value(by), value=value)
            )
            if handle is not None
        ]

    def tap(self, points: Sequence[tuple[float, float]], duration: int = 0) -> None:
        self._remote.tap(points, duration=duration)

    def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration: int = 0,
    ) -> None:
        self._remote.swipe(start_x, start_y, end_x, end_y, duration=duration)

    def double_tap(
        self,
        x: float,
        y: float,
        interval: float,
        duration_ms: tuple[int, int],
    ) -> bool:
        """Both taps in one W3C pointer batch, or two sequential ``tap`` calls."""
        if self._emit_w3c_double_tap(x, y, interval):
            return True
        tap = getattr(self._remote, "tap", None)
        if not callable(tap):
            return False
        duration = random.randint(duration_ms[0], duration_ms[1])
        tap([(x, y)], duration=duration)
        time.sleep(interval)
        tap([(x, y)], duration=duration)
        return True

    def _emit_w3c_double_tap(self, x: float, y: float, interval: float) -> bool:
        """Emit both taps as one W3C pointer batch; ``False`` when unsupported."""
        action_chains_cls = _load_w3c_action_chains()
        if action_chains_cls is None:
            return False
        try:
            actions = action_chains_cls(self._remote)
            (
                actions.w3c_actions.pointer_action.move_to_location(x, y)
                .pointer_down()
                .pointer_up()
                .pause(interval)
                .pointer_down()
                .pointer_up()
            )
            actions.perform()
        except Exception as exc:
            ui_logger.debug("[UI] double_click W3C batch unavailable: %s", exc)
            return False
        return True

    def activate_app(self, app_id: str) -> None:
        self._remote.activate_app(app_id)

    def press_keycode(self, keycode: int) -> None:
        self._remote.press_keycode(keycode)

    def back(self) -> None:
        self._remote.back()

    def get_window_size(self) -> dict[str, int]:
        return self._remote.get_window_size()

    @property
    def current_package(self) -> str | None:
        return self._remote.current_package

    def quit(self) -> None:
        self._remote.quit()


@dataclass
class DriverConfig:
    server_url: str = "http://127.0.0.1:4723"
    platform_name: str = "Android"
    automation_name: str = "UiAutomator2"
    device_name: str = "Android Emulator"
    app_package: str | None = None
    app_activity: str | None = None
    no_reset: bool = True
    auto_grant_permissions: bool = True
    new_command_timeout: int = 300
    extra_capabilities: dict[str, Any] = field(default_factory=dict)

    def to_options(self) -> UiAutomator2Options:
        options = UiAutomator2Options()
        options.platform_name = self.platform_name
        options.automation_name = self.automation_name
        options.device_name = self.device_name
        if self.app_package:
            options.app_package = self.app_package
        if self.app_activity:
            options.app_activity = self.app_activity
        options.no_reset = self.no_reset
        options.auto_grant_permissions = self.auto_grant_permissions
        options.new_command_timeout = self.new_command_timeout
        for k, v in self.extra_capabilities.items():
            options.set_capability(k, v)
        return options


class AppiumSession:
    """Manages an active Appium WebDriver session."""

    def __init__(self, config: DriverConfig):
        self.config = config
        self.driver: AppiumDriver | None = None

    def start(self) -> AppiumDriver:
        """Initialize and connect to the Appium server."""
        options = self.config.to_options()
        executor = self.config.server_url
        if "://0.0.0.0" in executor:
            executor = executor.replace("://0.0.0.0", "://127.0.0.1")
        self.driver = AppiumDriver(webdriver.Remote(command_executor=executor, options=options))
        if self.config.app_package:
            with contextlib.suppress(Exception):
                self.driver.activate_app(self.config.app_package)
        return self.driver

    def activate_app(self, app_id: str | None = None) -> bool:
        """Activate the specified application or the configured app_package."""
        target = app_id or self.config.app_package
        if target and self.driver:
            try:
                self.driver.activate_app(target)
                return True
            except Exception:
                pass
        return False

    def stop(self) -> None:
        """Gracefully terminate the WebDriver session."""
        if self.driver:
            with contextlib.suppress(Exception):
                self.driver.quit()
            self.driver = None

    def is_active(self) -> bool:
        return self.driver is not None


__all__ = ["AppiumDriver", "AppiumSession", "DriverConfig"]
