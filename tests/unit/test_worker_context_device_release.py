"""
tests/unit/test_worker_context_device_release.py
================================================
Unit tests for releasing a Virtual Device Session during worker shutdown
(spec #218, ticket #220). The release path must never raise and must never claim
to have closed a session it was never handed.
"""

import logging

from boss_agent.adapters.inbound.worker.config import WorkerConfig
from boss_agent.adapters.inbound.worker.context import WorkerContext
from boss_agent.adapters.memory.automation import FakeDeviceSession, FakeUIDriver


def _context(session: FakeDeviceSession | None = None) -> WorkerContext:
    """A context whose lazy session factory hands back ``session``."""
    return WorkerContext(
        config=WorkerConfig(worker_id="release-test"),
        session_factory=lambda: session if session is not None else FakeDeviceSession(),
    )


def test_release_stops_the_session_and_drops_the_reference():
    """Verify an opened session is stopped exactly once and then forgotten."""
    session = FakeDeviceSession()
    context = _context(session)

    assert context.driver is session.driver
    assert context.has_device_session() is True
    assert context.release_device_session() is True

    assert session.stopped is True
    assert context.has_device_session() is False
    assert context.release_device_session() is False, "a released session must not be re-closed"


def test_release_reports_false_when_nothing_was_opened():
    """Verify releasing an unopened context closes nothing and claims nothing."""
    context = _context()

    assert context.release_device_session() is False


def test_release_reports_false_for_a_driver_injected_without_a_session(caplog):
    """Verify an injected driver with no session is dropped and reported honestly."""
    context = WorkerContext(config=WorkerConfig(worker_id="release-test"), driver=FakeUIDriver())

    with caplog.at_level(logging.WARNING, logger="boss_agent.worker"):
        released = context.release_device_session()

    assert released is False, "nothing was closed, so the release must not claim success"
    assert context.has_device_session() is False, "the reference must still be dropped"
    assert any("without a session" in record.message for record in caplog.records)


def test_release_swallows_session_stop_errors():
    """Verify a failing teardown cannot raise out of the shutdown path."""

    class ExplodingSession(FakeDeviceSession):
        def stop(self) -> None:
            raise TimeoutError("appium server not responding")

    context = _context(ExplodingSession())

    assert context.driver is not None
    assert context.release_device_session() is True


def test_has_device_session_does_not_open_a_lazy_session():
    """Verify probing for a session never opens one."""
    session = FakeDeviceSession()
    context = _context(session)

    assert context.has_device_session() is False
    assert session.started is False
