"""
tests/unit/test_automation_use_cases.py
=======================================
In-memory tests for the automation use cases, the Android dialog interceptor, the
domain ``BoundingBox``, and the composition root's device builders (spec:
integrate-droid-agent-core). No device, network, or disk I/O.
"""

import pytest

from boss_agent import main as composition
from boss_agent.adapters.memory.automation import (
    FakeAdbClient,
    FakeDeviceSession,
    FakeUIDriver,
    FakeUIElement,
    InMemoryLocatorStore,
)
from boss_agent.adapters.memory.llm_client import StubLLMDecisionClient
from boss_agent.adapters.outbound.config.locator_registry import YamlLocatorRegistry
from boss_agent.adapters.outbound.device.appium.interceptors import (
    AndroidSystemDialogInterceptor,
)
from boss_agent.adapters.outbound.device.appium.session import AppiumSession
from boss_agent.core.automation.domain.errors import ElementWaitTimeoutError
from boss_agent.core.automation.domain.geometry import BoundingBox
from boss_agent.core.automation.domain.locators import By, UISelector, ViewDescriptor
from boss_agent.core.automation.use_cases.interceptors import InterceptorRegistry
from boss_agent.core.automation.use_cases.view_probe import ViewPresenceChecker

# ----------------------------------------------------------------- view presence


def _view(*selectors: UISelector) -> ViewDescriptor:
    return ViewDescriptor(name="home", required_selectors=list(selectors))


def test_view_presence_requires_every_required_selector():
    driver = FakeUIDriver()
    driver.elements[("id", "com.example:id/title")] = [FakeUIElement()]
    checker = ViewPresenceChecker(driver)

    assert checker.is_present(_view(UISelector(By.ID, "com.example:id/title"))) is True
    assert (
        checker.is_present(
            _view(
                UISelector(By.ID, "com.example:id/title"),
                UISelector(By.ID, "com.example:id/body"),
            )
        )
        is False
    )


def test_view_presence_treats_a_query_error_as_absent():
    class BrokenDriver(FakeUIDriver):
        def find_elements(self, by: str, value: str):
            raise RuntimeError("stale session")

    assert ViewPresenceChecker(BrokenDriver()).is_present(_view(UISelector(By.ID, "x"))) is False


def test_view_presence_without_a_driver_is_absent():
    assert ViewPresenceChecker(None).is_present(_view(UISelector(By.ID, "x"))) is False


def test_wait_until_present_times_out_on_the_injected_clock():
    driver = FakeUIDriver()
    ticks = iter([0.0, 0.0, 5.0])
    checker = ViewPresenceChecker(
        driver, clock=lambda: next(ticks, 5.0), sleep=lambda _seconds: None
    )

    with pytest.raises(ElementWaitTimeoutError):
        checker.wait_until_present(_view(UISelector(By.ID, "never")), timeout_sec=1.0)


# ------------------------------------------------------------------ interceptors


class _RecordingInterceptor:
    def __init__(self, name: str, handles: bool, result: bool) -> None:
        self.name = name
        self.handles = handles
        self.result = result
        self.seen: list[str] = []

    def can_handle(self, driver) -> bool:
        self.seen.append(self.name)
        return self.handles

    def handle(self, driver) -> bool:
        self.seen.append(f"{self.name}:handled")
        return self.result


def test_interceptor_registry_short_circuits_on_the_first_match():
    first = _RecordingInterceptor("first", handles=True, result=True)
    second = _RecordingInterceptor("second", handles=True, result=False)
    registry = InterceptorRegistry([first, second])

    assert registry.process_all(FakeUIDriver()) is True
    assert first.seen == ["first", "first:handled"]
    assert second.seen == []


def test_interceptor_registry_returns_false_when_none_match():
    registry = InterceptorRegistry([_RecordingInterceptor("only", handles=False, result=True)])

    assert registry.process_all(FakeUIDriver()) is False


def test_interceptor_registry_can_register_after_construction():
    registry = InterceptorRegistry()
    registry.register(_RecordingInterceptor("late", handles=True, result=True))

    assert registry.process_all(FakeUIDriver()) is True


def test_android_dialog_interceptor_dismisses_a_configured_permission_button():
    store = InMemoryLocatorStore(
        {"system_dialog": {"permission_buttons": ["com.example:id/allow"]}}
    )
    button = FakeUIElement()
    driver = FakeUIDriver()
    driver.elements[("id", "com.example:id/allow")] = [button]
    interceptor = AndroidSystemDialogInterceptor(store)

    assert interceptor.can_handle(driver) is True
    assert interceptor.handle(driver) is True
    assert button.click_count == 1


def test_android_dialog_interceptor_falls_back_to_android_resource_ids():
    button = FakeUIElement()
    driver = FakeUIDriver()
    driver.elements[("id", "com.android.permissioncontroller:id/permission_allow_button")] = [
        button
    ]
    interceptor = AndroidSystemDialogInterceptor(InMemoryLocatorStore())

    assert interceptor.can_handle(driver) is True
    assert interceptor.handle(driver) is True
    assert button.click_count == 1


# ------------------------------------------------------------------- domain box


def test_bounding_box_from_rect_coerces_to_floats():
    box = BoundingBox.from_rect({"x": 10, "y": 20, "width": 30.5, "height": 40})

    assert box == BoundingBox(x=10.0, y=20.0, width=30.5, height=40.0)


def test_bounding_box_from_rect_requires_every_edge():
    with pytest.raises(KeyError):
        BoundingBox.from_rect({"x": 0, "y": 0, "width": 10})


# ------------------------------------------------------------------ memory fakes


def test_stub_llm_decision_client_scripts_and_records_both_protocols():
    stub = StubLLMDecisionClient(text="hello", data={"pass": True})

    assert stub.chat_completion([{"role": "user", "content": "hi"}]) == "hello"
    assert stub.chat_completion_json([{"role": "user", "content": "hi"}]) == {"pass": True}
    assert stub.chat_calls[0]["messages"][0]["content"] == "hi"
    assert stub.json_calls[0]["messages"][0]["content"] == "hi"


def test_stub_llm_decision_client_honours_the_json_callback():
    stub = StubLLMDecisionClient(on_json=lambda **_kwargs: {"scripted": True})

    assert stub.chat_completion_json([]) == {"scripted": True}


def test_fake_device_session_reports_its_lifecycle():
    driver = FakeUIDriver()
    session = FakeDeviceSession(driver)

    assert session.is_active() is False
    assert session.start() is driver
    assert session.is_active() is True
    session.stop()
    assert session.is_active() is False


def test_fake_adb_client_records_commands_and_replays_stdout():
    adb = FakeAdbClient(stdout=" com.hpbr.bosszhipin/.MainActivity \n")

    assert adb.get_current_focus() == "com.hpbr.bosszhipin/.MainActivity"
    assert adb.commands == [["shell", "dumpsys", "window"]]


def test_in_memory_locator_store_reads_nested_keys_with_defaults():
    store = InMemoryLocatorStore({"job_list": {"search_icon": "com.example:id/menu"}})

    assert store.get_raw("job_list.search_icon") == "com.example:id/menu"
    assert store.get_raw("job_list.missing", default="fallback") == "fallback"


# ------------------------------------------------------------- composition root


def test_build_locator_store_returns_the_yaml_port():
    assert isinstance(composition.build_locator_store(), YamlLocatorRegistry)


def test_build_interceptor_registry_wires_the_android_dialog_interceptor():
    registry = composition.build_interceptor_registry(InMemoryLocatorStore())

    assert isinstance(registry, InterceptorRegistry)
    assert isinstance(registry.interceptors[0], AndroidSystemDialogInterceptor)


def test_build_device_session_returns_a_configured_session_port():
    session = composition.build_device_session(
        device_udid="emulator-5554", server_url="http://127.0.0.1:4723"
    )

    assert isinstance(session, AppiumSession)
    assert session.config.device_name == "emulator-5554"
    assert session.config.app_package == composition.BOSS_APP_PACKAGE
