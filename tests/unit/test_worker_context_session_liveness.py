"""
tests/unit/test_worker_context_session_liveness.py
===================================================
Unit tests for Virtual Device Session liveness verification (#287-adjacent).

Appium terminates a session after `new_command_timeout` of silence, so a worker that
idles between tasks wakes up to a driver whose session is already gone. Every element
probe against such a driver fails, the page objects swallow the failure into `None`,
and the task dies reporting a UI problem that never existed. The context must notice
the dead session before a handler runs and let the factory replace it.
"""

from unittest.mock import MagicMock

import pytest
from selenium.common.exceptions import WebDriverException

from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext


class _DeadDriver:
    """A driver whose session the Appium server has already terminated."""

    def __init__(self) -> None:
        self.quit_called = False

    @property
    def current_package(self) -> str:
        raise WebDriverException("A session is either terminated or not started")

    def quit(self) -> None:
        self.quit_called = True


def _live_driver() -> MagicMock:
    driver = MagicMock()
    driver.current_package = "com.hpbr.bosszhipin"
    return driver


def test_ensure_keeps_a_live_session_and_never_reopens():
    """A session that answers the probe is kept as-is; the factory stays untouched."""
    driver = _live_driver()
    factory = MagicMock()
    context = WorkerContext(
        config=WorkerConfig(worker_id="live"), driver=driver, driver_factory=factory
    )

    assert context.ensure_device_session() is True

    assert context.driver is driver
    factory.assert_not_called()
    driver.quit.assert_not_called()


def test_ensure_drops_a_dead_session_so_the_factory_reopens_it():
    """A terminated session is released, and the next driver access builds a fresh one."""
    dead = _DeadDriver()
    fresh = _live_driver()
    factory = MagicMock(return_value=fresh)
    context = WorkerContext(
        config=WorkerConfig(worker_id="dead"), driver=dead, driver_factory=factory
    )

    assert context.ensure_device_session() is False

    assert dead.quit_called is True, "the dead session must be released, not leaked"
    assert context.driver is fresh
    factory.assert_called_once()


def test_ensure_on_a_lazy_context_opens_nothing():
    """Verifying before any session exists must not resolve the factory."""
    factory = MagicMock()
    context = WorkerContext(config=WorkerConfig(worker_id="lazy"), driver_factory=factory)

    assert context.ensure_device_session() is True
    factory.assert_not_called()


def test_ensure_keeps_a_driver_that_exposes_no_probe():
    """A driver without the probe surface is kept: an unknown probe is not a dead session."""
    driver = MagicMock(spec=["quit"])
    context = WorkerContext(config=WorkerConfig(worker_id="noprobesurface"), driver=driver)

    assert context.ensure_device_session() is True
    driver.quit.assert_not_called()


class _ThrowingDriver:
    def __init__(self, error: Exception) -> None:
        self._error = error
        self.quit_called = False

    @property
    def current_package(self) -> str:
        raise self._error

    def quit(self) -> None:
        self.quit_called = True


@pytest.mark.parametrize("probe_error", [WebDriverException("gone"), OSError("connection reset")])
def test_ensure_treats_any_probe_failure_as_dead(probe_error):
    """The probe must not be exception-type-picky: a wedged transport is as dead as a 404."""
    driver = _ThrowingDriver(probe_error)
    context = WorkerContext(
        config=WorkerConfig(worker_id="pickye"),
        driver=driver,
        driver_factory=MagicMock(return_value=_live_driver()),
    )

    assert context.ensure_device_session() is False
    assert driver.quit_called is True
