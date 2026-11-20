"""Unit tests for the Appium adapter that implements the neutral driver port."""

import builtins

import pytest

from boss_agent.adapters import appium_driver as appium_adapter
from boss_agent.adapters.appium_driver import AppiumDriver
from boss_agent.automation.port import By


class FakeRemote:
    """The Appium ``Remote`` surface the adapter translates, and nothing else."""

    def __init__(self) -> None:
        self.taps: list = []
        self.queries: list = []
        self.current_package = "com.example.app"
        self.quit_called = False

    def tap(self, positions, duration=0):
        self.taps.append((list(positions), duration))

    def find_elements(self, by, value):
        self.queries.append((by, value))
        return []

    def quit(self) -> None:
        self.quit_called = True


class FakePointer:
    """Chainable stand-in for Selenium's W3C pointer action builder."""

    def __init__(self, calls: list) -> None:
        self._calls = calls

    def _record(self, name: str, *args) -> "FakePointer":
        self._calls.append((name, *args))
        return self

    def move_to_location(self, x, y, *_args, **_kwargs) -> "FakePointer":
        return self._record("move_to_location", x, y)

    def pointer_down(self, button=0, *_args, **_kwargs) -> "FakePointer":
        return self._record("pointer_down", button)

    def pointer_up(self, button=0) -> "FakePointer":
        return self._record("pointer_up", button)

    def pause(self, seconds=0) -> "FakePointer":
        return self._record("pause", seconds)


class FakeW3CActions:
    """Stand-in for the `ActionBuilder` reachable at `ActionChains.w3c_actions`."""

    def __init__(self, pointer: FakePointer) -> None:
        self.pointer_action = pointer


class FakeActionChains:
    """Stand-in for `ActionChains`: records pointer steps and each `perform()` batch."""

    batches: list = []
    pointer_calls: list = []
    last_driver = None

    def __init__(self, driver) -> None:
        type(self).last_driver = driver
        self.pointer = FakePointer(type(self).pointer_calls)
        self.w3c_actions = FakeW3CActions(self.pointer)

    def perform(self) -> None:
        type(self).batches.append(list(type(self).pointer_calls))
        type(self).pointer_calls = []


class RaisingActionChains(FakeActionChains):
    """W3C batch that always fails, standing in for a driver without pointer support."""

    def perform(self) -> None:
        raise RuntimeError("unhandled inspector error: W3C actions unsupported")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(appium_adapter.time, "sleep", lambda _seconds: None)


@pytest.fixture
def w3c_supported(monkeypatch):
    """Make the W3C pointer seam available and hand back the recorded batches."""
    FakeActionChains.batches = []
    FakeActionChains.pointer_calls = []
    monkeypatch.setattr(appium_adapter, "_load_w3c_action_chains", lambda: FakeActionChains)
    return FakeActionChains


def test_double_tap_prefers_one_w3c_batch(w3c_supported):
    remote = FakeRemote()

    assert AppiumDriver(remote).double_tap(540.0, 1200.0, 0.15, (60, 120)) is True

    assert len(w3c_supported.batches) == 1
    steps = [name for name, *_rest in w3c_supported.batches[0]]
    assert steps == [
        "move_to_location",
        "pointer_down",
        "pointer_up",
        "pause",
        "pointer_down",
        "pointer_up",
    ]
    assert remote.taps == [], "the W3C path must not also fall back to driver.tap"
    assert w3c_supported.last_driver is remote


def test_double_tap_falls_back_to_tap_when_the_batch_raises(monkeypatch):
    monkeypatch.setattr(appium_adapter, "_load_w3c_action_chains", lambda: RaisingActionChains)
    remote = FakeRemote()

    assert AppiumDriver(remote).double_tap(540.0, 1200.0, 0.15, (60, 120)) is True

    assert len(remote.taps) == 2, "a failed W3C batch must degrade to driver.tap"


def test_double_tap_falls_back_to_tap_without_action_chains(monkeypatch):
    monkeypatch.setattr(appium_adapter, "_load_w3c_action_chains", lambda: None)
    remote = FakeRemote()

    assert AppiumDriver(remote).double_tap(540.0, 1200.0, 0.15, (60, 120)) is True

    assert len(remote.taps) == 2
    for positions, _duration in remote.taps:
        assert positions == [(540.0, 1200.0)]


def test_double_tap_reports_false_when_the_remote_has_no_tap(monkeypatch):
    monkeypatch.setattr(appium_adapter, "_load_w3c_action_chains", lambda: None)

    class NoTapRemote:
        pass

    assert AppiumDriver(NoTapRemote()).double_tap(0, 0, 0.15, (60, 120)) is False


def test_find_elements_translates_the_neutral_strategy():
    remote = FakeRemote()
    driver = AppiumDriver(remote)

    driver.find_elements(By.ID, "com.example:id/button")
    driver.find_elements("xpath", "//*[@text='ok']")

    assert remote.queries == [
        ("id", "com.example:id/button"),
        ("xpath", "//*[@text='ok']"),
    ]


def test_the_adapter_forwards_the_rest_of_the_port_surface():
    remote = FakeRemote()
    driver = AppiumDriver(remote)

    assert driver.current_package == "com.example.app"

    driver.quit()
    assert remote.quit_called is True


class FakeElement:
    """The raw WebElement surface the adapter wraps, and nothing else."""

    def __init__(self, text: str = "Continue") -> None:
        self.text = text
        self.class_name = "android.widget.Button"
        self.rect = {"x": 1, "y": 2, "width": 3, "height": 4}
        self.queries: list = []

    def click(self) -> None:
        self.clicked = True

    def send_keys(self, text: str) -> None:
        self.sent = text

    def clear(self) -> None:
        self.cleared = True

    def get_attribute(self, name: str):
        return "true"

    def find_element(self, by, value):
        self.queries.append((by, value))
        return FakeElement("child")

    def find_elements(self, by, value):
        self.queries.append((by, value))
        return [FakeElement("child")]


class ElementRemote(FakeRemote):
    def __init__(self, elements: list[FakeElement]) -> None:
        super().__init__()
        self._elements = elements

    def find_elements(self, by, value):
        self.queries.append((by, value))
        return self._elements


def test_elements_are_wrapped_in_the_neutral_handle():
    raw = FakeElement()
    driver = AppiumDriver(ElementRemote([raw]))

    elements = driver.find_elements(By.ID, "com.example:id/button")

    assert len(elements) == 1
    handle = elements[0]
    assert handle.text == "Continue"
    assert handle.rect == {"x": 1, "y": 2, "width": 3, "height": 4}
    assert handle.find_elements(By.XPATH, "//child")[0].text == "child"
    assert raw.queries == [("xpath", "//child")]


def test_non_port_members_are_not_proxied():
    """Only the port surface is reachable; the raw Remote is not leaked through getattr."""
    driver = AppiumDriver(FakeRemote())

    with pytest.raises(AttributeError):
        driver.get_page_source  # noqa: B018 - the access is the assertion


def test_w3c_action_chains_is_resolved_once_and_cached(monkeypatch):
    """The import machinery is not per-call work, and the answer cannot change mid-run."""
    monkeypatch.setattr(appium_adapter, "_w3c_action_chains", appium_adapter._unresolved)
    probes = 0

    def counting_resolver():
        nonlocal probes
        probes += 1
        return FakeActionChains

    monkeypatch.setattr(appium_adapter, "_resolve_w3c_action_chains", counting_resolver)

    assert appium_adapter._load_w3c_action_chains() is FakeActionChains
    assert appium_adapter._load_w3c_action_chains() is FakeActionChains
    assert appium_adapter._load_w3c_action_chains() is FakeActionChains

    assert probes == 1, "the client is probed once, not once per double-tap"


def test_w3c_action_chains_resolver_prefers_appium_then_falls_back_to_selenium(monkeypatch):
    """Appium-Python-Client 6.0.0 ships no action_chains, so selenium is the live branch."""
    real_import = builtins.__import__
    attempted: list[str] = []

    def tracking_import(name, *args, **kwargs):
        if name.endswith("action_chains"):
            attempted.append(name)
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", tracking_import)

    assert appium_adapter._resolve_w3c_action_chains() is None
    assert attempted == [
        "appium.webdriver.common.action_chains",
        "selenium.webdriver.common.action_chains",
    ]
