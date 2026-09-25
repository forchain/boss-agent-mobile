"""
tests/unit/test_worker_context_device_release.py
================================================
Unit tests for releasing a Virtual Device Session during worker shutdown
(spec #218, ticket #220). The release path must never raise and must never claim
to have closed a session it never handed to a closer.
"""

import logging
from unittest.mock import MagicMock

import pytest

from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext


def _context(driver) -> WorkerContext:
    return WorkerContext(config=WorkerConfig(worker_id="release-test"), driver=driver)


def test_release_closes_appium_driver_and_drops_the_reference():
    """Verify a live Appium driver is quit exactly once and then forgotten."""
    driver = MagicMock()
    context = _context(driver)

    assert context.has_device_session() is True
    assert context.release_device_session() is True

    driver.quit.assert_called_once()
    assert context.has_device_session() is False
    assert context.release_device_session() is False, "a released session must not be re-closed"


def test_release_falls_back_to_stop_for_a_session_wrapper():
    """Verify a driver exposing only `stop()` (an AppiumSession wrapper) is still released."""

    class Wrapper:
        stopped = False

        def stop(self) -> None:
            self.stopped = True

    wrapper = Wrapper()
    assert _context(wrapper).release_device_session() is True
    assert wrapper.stopped is True


def test_release_reports_false_when_the_driver_exposes_no_closer(caplog):
    """Verify an unclosable driver is reported honestly rather than as a clean release."""
    context = _context(object())

    with caplog.at_level(logging.WARNING, logger="boss_agent.worker"):
        released = context.release_device_session()

    assert released is False, "nothing was closed, so the release must not claim success"
    assert context.has_device_session() is False, "the reference must still be dropped"
    assert any("no closer" in record.message for record in caplog.records)


def test_release_swallows_closer_errors():
    """Verify a failing teardown cannot raise out of the shutdown path."""
    driver = MagicMock()
    driver.quit.side_effect = TimeoutError("appium server not responding")

    assert _context(driver).release_device_session() is True


def test_has_device_session_does_not_resolve_a_lazy_factory():
    """Verify probing for a session never opens one."""
    factory = MagicMock()
    context = WorkerContext(config=WorkerConfig(worker_id="release-test"), driver_factory=factory)

    assert context.has_device_session() is False
    factory.assert_not_called()


@pytest.mark.parametrize("closer_name", ["quit", "stop"])
def test_release_prefers_the_widest_supported_closer(closer_name):
    """Verify the documented closer precedence is respected."""
    driver = MagicMock(spec=[closer_name])
    parent = _context(driver)

    assert parent.release_device_session() is True
    getattr(driver, closer_name).assert_called_once()
