"""
tests/unit/test_worker_graceful_shutdown.py
==========================================
Unit tests for the Automation Worker signal & broker cancellation seam
(spec #218, ticket #220). No live device is required: signal delivery is
simulated by invoking the worker's shutdown protocol directly.
"""

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from boss_agent.broker.models import TaskStatus, TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.daemon import SHUTDOWN_CANCEL_REASON, AutomationWorker
from boss_agent.worker.handlers.base import BaseTaskHandler, HandlerResult

SIGNAL_ACK_LOG = "🛑 [Worker] Received shutdown signal (SIGTERM), initiating graceful shutdown..."
SHUTDOWN_COMPLETE_LOG = "👋 [Worker] Shutdown complete."


class BlockingHandler(BaseTaskHandler):
    """Handler that parks mid-automation until the worker cancels it."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False
        self.driver_touched = False

    @property
    def task_type(self) -> TaskType:
        return TaskType.SCRAPE_JOBS

    async def handle(self, task, broker, context) -> HandlerResult:
        # Real handlers resolve the device session lazily; mirror that here so the
        # shutdown path has an established session to release.
        _ = context.driver
        self.driver_touched = True
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return HandlerResult(success=True)


@pytest.fixture
def broker():
    return InMemoryTaskBroker()


@pytest.fixture
def mock_driver():
    return MagicMock()


def _build_worker(broker, handler, driver_factory=None, driver=None, poll_interval=0.01):
    config = WorkerConfig(
        worker_id="worker-shutdown-test",
        device_id="emulator-5554",
        poll_interval_sec=poll_interval,
        heartbeat_interval_sec=0.05,
    )
    context = WorkerContext(config=config, driver=driver, driver_factory=driver_factory)
    return AutomationWorker(config=config, broker=broker, context=context, handlers=[handler])


@pytest.mark.asyncio
async def test_shutdown_logs_signal_acknowledgment_and_completion(broker, caplog):
    """Verify an idle worker logs the signal acknowledgment and final completion notice."""
    import logging

    worker = _build_worker(broker, BlockingHandler())

    with caplog.at_level(logging.INFO, logger="boss_agent.worker"):
        await worker.shutdown("SIGTERM")

    messages = [r.message for r in caplog.records if r.name == "boss_agent.worker"]
    assert SIGNAL_ACK_LOG in messages
    assert SHUTDOWN_COMPLETE_LOG in messages


@pytest.mark.asyncio
async def test_shutdown_does_not_open_device_session_when_idle(broker, mock_driver):
    """Verify shutdown never lazily opens a Virtual Device Session it then has to release."""
    driver_factory = MagicMock(return_value=mock_driver)
    worker = _build_worker(broker, BlockingHandler(), driver_factory=driver_factory)

    await worker.shutdown("SIGTERM")

    driver_factory.assert_not_called()
    mock_driver.quit.assert_not_called()


@pytest.mark.asyncio
async def test_shutdown_cancels_in_flight_task_and_aborts_handler(broker, mock_driver):
    """Verify an executing handler is aborted rather than left driving the device."""
    handler = BlockingHandler()
    worker = _build_worker(broker, handler, driver=mock_driver)
    await broker.create_task(task_type=TaskType.SCRAPE_JOBS)

    loop_task = asyncio.create_task(worker.start())
    await asyncio.wait_for(handler.started.wait(), timeout=2.0)

    await worker.shutdown("SIGTERM")

    assert handler.cancelled is True, "handler must be aborted mid-execution"
    await asyncio.wait_for(loop_task, timeout=2.0)


@pytest.mark.asyncio
async def test_shutdown_marks_in_flight_task_cancelled_in_broker(broker, mock_driver):
    """Verify the in-flight task is transitioned to CANCELLED with a descriptive reason."""
    handler = BlockingHandler()
    worker = _build_worker(broker, handler, driver=mock_driver)
    task = await broker.create_task(task_type=TaskType.SCRAPE_JOBS)

    loop_task = asyncio.create_task(worker.start())
    await asyncio.wait_for(handler.started.wait(), timeout=2.0)

    await worker.shutdown("SIGTERM")

    stored = await broker.get_task(task.id)
    assert stored is not None
    assert stored.status == TaskStatus.CANCELLED
    assert stored.error_message == SHUTDOWN_CANCEL_REASON
    assert any(SHUTDOWN_CANCEL_REASON in line for line in stored.logs)
    await asyncio.wait_for(loop_task, timeout=2.0)


@pytest.mark.asyncio
async def test_shutdown_releases_active_device_session(broker, mock_driver):
    """Verify the active Appium session is closed so the device is left stable."""
    handler = BlockingHandler()
    worker = _build_worker(broker, handler, driver=mock_driver)
    await broker.create_task(task_type=TaskType.SCRAPE_JOBS)

    loop_task = asyncio.create_task(worker.start())
    await asyncio.wait_for(handler.started.wait(), timeout=2.0)

    await worker.shutdown("SIGTERM")

    assert handler.driver_touched is True
    mock_driver.quit.assert_called_once()
    await asyncio.wait_for(loop_task, timeout=2.0)


@pytest.mark.asyncio
async def test_shutdown_completes_quickly_even_when_device_release_hangs(broker, caplog):
    """Verify a wedged Appium session cannot hold the shutdown sequence hostage."""
    import logging

    hanging_driver = MagicMock()
    hanging_driver.quit.side_effect = TimeoutError("appium server not responding")
    handler = BlockingHandler()
    worker = _build_worker(broker, handler, driver=hanging_driver)
    await broker.create_task(task_type=TaskType.SCRAPE_JOBS)

    loop_task = asyncio.create_task(worker.start())
    await asyncio.wait_for(handler.started.wait(), timeout=2.0)

    started_at = time.monotonic()
    with caplog.at_level(logging.INFO, logger="boss_agent.worker"):
        await worker.shutdown("SIGTERM")
    elapsed = time.monotonic() - started_at

    assert elapsed < 5.0, f"shutdown took {elapsed:.2f}s, exceeding the 5s budget"
    messages = [r.message for r in caplog.records if r.name == "boss_agent.worker"]
    assert SHUTDOWN_COMPLETE_LOG in messages
    await asyncio.wait_for(loop_task, timeout=2.0)


@pytest.mark.asyncio
async def test_shutdown_stops_polling_loop_without_waiting_for_poll_interval(broker, mock_driver):
    """Verify the idle poll sleep is interrupted so shutdown does not linger."""
    worker = _build_worker(broker, BlockingHandler(), driver=mock_driver, poll_interval=30.0)

    loop_task = asyncio.create_task(worker.start())
    await asyncio.sleep(0.05)

    started_at = time.monotonic()
    await worker.shutdown("SIGTERM")
    await asyncio.wait_for(loop_task, timeout=2.0)
    elapsed = time.monotonic() - started_at

    assert elapsed < 1.0, (
        f"shutdown waited {elapsed:.2f}s for the 30s poll interval instead of interrupting it"
    )


@pytest.mark.asyncio
async def test_shutdown_is_idempotent(broker, mock_driver, caplog):
    """Verify repeated termination signals do not raise or double-release the device."""
    import logging

    handler = BlockingHandler()
    worker = _build_worker(broker, handler, driver=mock_driver)
    await broker.create_task(task_type=TaskType.SCRAPE_JOBS)

    loop_task = asyncio.create_task(worker.start())
    await asyncio.wait_for(handler.started.wait(), timeout=2.0)

    with caplog.at_level(logging.INFO, logger="boss_agent.worker"):
        await worker.shutdown("SIGTERM")
        await worker.shutdown("SIGINT")

    mock_driver.quit.assert_called_once()
    await asyncio.wait_for(loop_task, timeout=2.0)


@pytest.mark.asyncio
async def test_shutdown_does_not_overwrite_a_task_that_already_succeeded(broker, mock_driver):
    """Verify an abort racing a handler's own completion leaves the terminal status alone."""
    handler = BlockingHandler()
    worker = _build_worker(broker, handler, driver=mock_driver)
    task = await broker.create_task(task_type=TaskType.SCRAPE_JOBS)
    await broker.update_task_status(task.id, status=TaskStatus.SUCCESS)

    # Simulate a shutdown landing in the window after the handler recorded its outcome but
    # before the in-flight handle was cleared.
    worker._inflight_task_id = task.id
    worker._inflight_exec_task = asyncio.create_task(asyncio.sleep(0))

    await worker.shutdown("SIGTERM")

    stored = await broker.get_task(task.id)
    assert stored is not None
    assert stored.status == TaskStatus.SUCCESS, "shutdown overwrote a completed task"
    assert stored.error_message is None
    assert not any(SHUTDOWN_CANCEL_REASON in line for line in stored.logs)


@pytest.mark.asyncio
async def test_shutdown_still_cancels_after_a_blocking_synchronous_step(
    broker, mock_driver, caplog
):
    """Verify a handler that blocked the event loop synchronously is still aborted afterwards."""
    import logging

    class SyncBlockingHandler(BaseTaskHandler):
        """Blocks the loop the way a synchronous Appium command does, then parks."""

        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = False

        @property
        def task_type(self) -> TaskType:
            return TaskType.SCRAPE_JOBS

        async def handle(self, task, broker, context) -> HandlerResult:
            _ = context.driver
            self.started.set()
            time.sleep(0.3)  # no await point: the loop cannot dispatch the signal yet
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            return HandlerResult(success=True)

    handler = SyncBlockingHandler()
    worker = _build_worker(broker, handler, driver=mock_driver)
    task = await broker.create_task(task_type=TaskType.SCRAPE_JOBS)

    loop_task = asyncio.create_task(worker.start())
    await asyncio.wait_for(handler.started.wait(), timeout=2.0)
    await asyncio.sleep(0.4)  # let the synchronous step finish and the handler park

    started_at = time.monotonic()
    with caplog.at_level(logging.INFO, logger="boss_agent.worker"):
        await worker.shutdown("SIGTERM")
    elapsed = time.monotonic() - started_at

    assert elapsed < 5.0, f"shutdown took {elapsed:.2f}s after the blocking step"
    assert handler.cancelled is True
    stored = await broker.get_task(task.id)
    assert stored is not None
    assert stored.status == TaskStatus.CANCELLED
    await asyncio.wait_for(loop_task, timeout=2.0)
