"""
Unit tests for the Appium/ADB adapters, with WebDriver, ActionChains, and subprocess
mocked at the boundary (spec: integrate-droid-agent-core).
"""

import subprocess

import pytest

from boss_agent.adapters.outbound.device.appium import adb_client, driver_adapter, session
from boss_agent.adapters.outbound.device.appium.adb_client import SubprocessAdbClient
from boss_agent.adapters.outbound.device.appium.config import DriverConfig
from boss_agent.adapters.outbound.device.appium.driver_adapter import (
    AppiumDriverAdapter,
    _resolve_w3c_action_chains,
)
from boss_agent.adapters.outbound.device.appium.session import AppiumSession
from boss_agent.core.automation.domain.errors import AdbCommandError, DeviceSessionError


class FakePointer:
    def __init__(self, calls) -> None:
        self._calls = calls

    def _record(self, name, *args):
        self._calls.append((name, *args))
        return self

    def move_to_location(self, x, y, *_a, **_k):
        return self._record("move_to_location", x, y)

    def pointer_down(self, button=0, *_a, **_k):
        return self._record("pointer_down", button)

    def pointer_up(self, button=0):
        return self._record("pointer_up", button)

    def pause(self, seconds=0):
        return self._record("pause", seconds)


class FakeActionChains:
    batches: list = []
    pointer_calls: list = []

    def __init__(self, driver) -> None:
        self.driver = driver
        self.w3c_actions = type(
            "W", (), {"pointer_action": FakePointer(type(self).pointer_calls)}
        )()

    def perform(self) -> None:
        type(self).batches.append(list(type(self).pointer_calls))
        type(self).pointer_calls = []


class RaisingActionChains(FakeActionChains):
    def perform(self) -> None:
        raise RuntimeError("W3C actions unsupported")


@pytest.fixture
def w3c(monkeypatch):
    FakeActionChains.batches = []
    FakeActionChains.pointer_calls = []
    monkeypatch.setattr(driver_adapter, "_load_w3c_action_chains", lambda: FakeActionChains)
    return FakeActionChains


def test_perform_double_tap_emits_one_pointer_batch(w3c):
    adapter = AppiumDriverAdapter(object())

    assert adapter.perform_double_tap(540.0, 1200.0, 0.15) is True
    assert len(w3c.batches) == 1
    assert [step[0] for step in w3c.batches[0]] == [
        "move_to_location",
        "pointer_down",
        "pointer_up",
        "pause",
        "pointer_down",
        "pointer_up",
    ]


def test_perform_double_tap_returns_false_when_the_batch_raises(monkeypatch):
    monkeypatch.setattr(driver_adapter, "_load_w3c_action_chains", lambda: RaisingActionChains)
    assert AppiumDriverAdapter(object()).perform_double_tap(0.0, 0.0, 0.1) is False


def test_perform_double_tap_returns_false_without_action_chains(monkeypatch):
    monkeypatch.setattr(driver_adapter, "_load_w3c_action_chains", lambda: None)
    assert AppiumDriverAdapter(object()).perform_double_tap(0.0, 0.0, 0.1) is False


def test_action_chains_resolver_prefers_appium_then_selenium(monkeypatch):
    import builtins

    real_import = builtins.__import__
    attempted: list[str] = []

    def tracking_import(name, *args, **kwargs):
        if name.endswith("action_chains"):
            attempted.append(name)
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", tracking_import)
    assert _resolve_w3c_action_chains() is None
    assert attempted == [
        "appium.webdriver.common.action_chains",
        "selenium.webdriver.common.action_chains",
    ]


def test_adapter_delegates_unknown_attributes_to_the_raw_driver():
    class Raw:
        current_package = "com.hpbr.bosszhipin"

        def quit(self):
            self.quit_called = True

    raw = Raw()
    adapter = AppiumDriverAdapter(raw)
    assert adapter.current_package == "com.hpbr.bosszhipin"
    adapter.quit()
    assert raw.quit_called is True


def test_session_start_returns_an_adapter_and_errors_are_translated(monkeypatch):
    class FakeRemote:
        def __init__(self, *args, **kwargs):
            self.activated = []

        def activate_app(self, package):
            self.activated.append(package)

    monkeypatch.setattr(session.webdriver, "Remote", FakeRemote)
    cfg = DriverConfig(app_package="com.hpbr.bosszhipin")
    driver = AppiumSession(cfg).start()

    assert isinstance(driver, AppiumDriverAdapter)
    assert driver.raw_driver.activated == ["com.hpbr.bosszhipin"]

    def boom(*args, **kwargs):
        raise RuntimeError("no server")

    monkeypatch.setattr(session.webdriver, "Remote", boom)
    with pytest.raises(DeviceSessionError):
        AppiumSession(cfg).start()


def test_subprocess_adb_client_maps_success_and_failure(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, "focus\n", ""),
    )
    result = SubprocessAdbClient("emulator-5554").run(["shell", "true"])
    assert result.stdout.strip() == "focus"
    assert result.returncode == 0

    def raise_oserror(*args, **kwargs):
        raise OSError("adb missing")

    monkeypatch.setattr(subprocess, "run", raise_oserror)
    with pytest.raises(AdbCommandError):
        adb_client.SubprocessAdbClient().run(["devices"])
