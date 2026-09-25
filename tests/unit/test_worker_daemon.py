"""
tests/unit/test_worker_daemon.py
================================
Unit tests for Out-of-Process Automation Worker Daemon & CHECK_LOGIN Handler (Issue #29).
"""

from unittest.mock import MagicMock

import pytest

from boss_agent.broker.models import TaskStatus, TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.daemon import AutomationWorker
from boss_agent.worker.handlers.check_login import CheckLoginHandler


@pytest.fixture
def broker():
    return InMemoryTaskBroker()


@pytest.fixture
def mock_driver():
    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}
    driver.find_elements.return_value = []
    return driver


@pytest.mark.asyncio
async def test_worker_polls_and_executes_check_login_success(broker, mock_driver):
    """Verify worker picks up a pending CHECK_LOGIN task and completes it successfully."""
    mock_home_elem = MagicMock()

    def mock_find(by, value):
        if "ly_menu" in value or "search" in value or "tv_tab" in value or "job" in value:
            return [mock_home_elem]
        return []

    mock_driver.find_elements.side_effect = mock_find

    config = WorkerConfig(
        worker_id="test-worker-1", poll_interval_sec=0.01, heartbeat_interval_sec=0.1
    )
    context = WorkerContext(config=config, driver=mock_driver)

    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[CheckLoginHandler()],
    )

    task = await broker.create_task(
        task_type=TaskType.CHECK_LOGIN, payload={"device_id": "emulator-5554"}
    )
    assert task.status == TaskStatus.PENDING

    # Run single cycle of worker
    executed = await worker.run_once()
    assert executed is True

    # Verify task state in broker
    finished_task = await broker.get_task(task.id)
    assert finished_task is not None
    assert finished_task.status == TaskStatus.SUCCESS
    assert finished_task.worker_id == "test-worker-1"
    assert any("authenticated" in log.lower() for log in finished_task.logs)


@pytest.mark.asyncio
async def test_worker_handles_unauthenticated_login_failure(broker, mock_driver):
    """Verify worker fails CHECK_LOGIN task when app is on login screen."""
    mock_login_elem = MagicMock()

    def mock_find(by, value):
        if "登录" in value or "login" in value:
            return [mock_login_elem]
        return []

    mock_driver.find_elements.side_effect = mock_find

    config = WorkerConfig(worker_id="test-worker-2", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)

    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[CheckLoginHandler()],
    )

    task = await broker.create_task(task_type=TaskType.CHECK_LOGIN)
    executed = await worker.run_once()
    assert executed is True

    finished_task = await broker.get_task(task.id)
    assert finished_task is not None
    assert finished_task.status == TaskStatus.FAILED
    assert finished_task.error_message is not None
    assert "not logged in" in finished_task.error_message.lower()


@pytest.mark.asyncio
async def test_worker_idle_when_no_pending_tasks(broker, mock_driver):
    """Verify worker returns False and stays idle when no tasks are queued."""
    config = WorkerConfig(worker_id="test-worker-3", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[CheckLoginHandler()],
    )

    executed = await worker.run_once()
    assert executed is False


@pytest.mark.asyncio
async def test_worker_preserves_cancelled_status(broker, mock_driver):
    """Verify worker preserves CANCELLED status if user cancelled the task mid-execution."""
    from boss_agent.worker.handlers.base import BaseTaskHandler, HandlerResult

    class FakeLongRunningHandler(BaseTaskHandler):
        @property
        def task_type(self) -> TaskType:
            return TaskType.SCRAPE_JOBS

        async def handle(self, task, broker, context) -> HandlerResult:
            # Simulate cancellation happening during task execution
            await broker.update_task_status(task.id, status=TaskStatus.CANCELLED)
            return HandlerResult(success=True)

    config = WorkerConfig(worker_id="test-worker-cancel", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[FakeLongRunningHandler()],
    )

    task = await broker.create_task(task_type=TaskType.SCRAPE_JOBS)
    executed = await worker.run_once()
    assert executed is True

    # Status must remain CANCELLED, not overwritten to SUCCESS
    finished_task = await broker.get_task(task.id)
    assert finished_task is not None
    assert finished_task.status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_worker_logs_task_claiming_progress_and_success(broker, mock_driver, caplog):
    """Verify worker logs task claiming, progress mirroring, and successful completion with duration."""
    import logging
    mock_home_elem = MagicMock()

    def mock_find(by, value):
        if "ly_menu" in value or "search" in value or "tv_tab" in value or "job" in value:
            return [mock_home_elem]
        return []

    mock_driver.find_elements.side_effect = mock_find

    config = WorkerConfig(worker_id="worker-log-test", device_id="emulator-5554", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[CheckLoginHandler()],
    )

    task = await broker.create_task(
        task_type=TaskType.CHECK_LOGIN, payload={"keyword": "python"}
    )

    with caplog.at_level(logging.INFO, logger="boss_agent.worker"):
        executed = await worker.run_once()

    assert executed is True
    log_records = [r.message for r in caplog.records if r.name == "boss_agent.worker"]

    # 1. Must log claimed task with ID, type, device ID, and payload preview
    assert any(
        task.id in msg and "CHECK_LOGIN" in msg and "emulator-5554" in msg and "python" in msg
        for msg in log_records
    )
    # 2. Must mirror handler progress logs (append_log calls)
    assert any("📝 [Task" in msg and task.id in msg for msg in log_records)
    # 3. Must log task completion with duration in seconds
    assert any(task.id in msg and "completed successfully in" in msg and "s" in msg for msg in log_records)


@pytest.mark.asyncio
async def test_worker_logs_task_failure(broker, mock_driver, caplog):
    """Verify worker logs error message and duration when a task fails."""
    import logging
    mock_login_elem = MagicMock()

    def mock_find(by, value):
        if "登录" in value or "login" in value:
            return [mock_login_elem]
        return []

    mock_driver.find_elements.side_effect = mock_find

    config = WorkerConfig(worker_id="worker-fail-test", device_id="emulator-5554", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[CheckLoginHandler()],
    )

    task = await broker.create_task(task_type=TaskType.CHECK_LOGIN)

    with caplog.at_level(logging.INFO, logger="boss_agent.worker"):
        executed = await worker.run_once()

    assert executed is True
    log_records = [r.message for r in caplog.records if r.name == "boss_agent.worker"]

    # Must log task failure with duration and error message
    assert any(task.id in msg and "failed in" in msg and "not logged in" in msg.lower() for msg in log_records)


@pytest.mark.asyncio
async def test_worker_logs_task_cancellation_and_exceptions(broker, mock_driver, caplog):
    """Verify worker logs cancellation and uncaught exceptions with duration."""
    import logging

    from boss_agent.worker.handlers.base import BaseTaskHandler, HandlerResult

    class ThrowingHandler(BaseTaskHandler):
        @property
        def task_type(self) -> TaskType:
            return TaskType.SCRAPE_JOBS

        async def handle(self, task, broker, context) -> HandlerResult:
            raise RuntimeError("Simulated crash during automation")

    config = WorkerConfig(worker_id="worker-crash-test", device_id="emulator-5554", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[ThrowingHandler()],
    )

    task = await broker.create_task(task_type=TaskType.SCRAPE_JOBS)

    with caplog.at_level(logging.INFO, logger="boss_agent.worker"):
        executed = await worker.run_once()

    assert executed is True
    log_records = [r.message for r in caplog.records if r.name == "boss_agent.worker"]
    assert any(task.id in msg and "uncaught exception in" in msg and "Simulated crash" in msg for msg in log_records)


@pytest.mark.asyncio
async def test_worker_start_logs_readiness(broker, mock_driver, caplog):
    """Verify worker start() emits a readiness log before beginning the loop."""
    import asyncio
    import logging

    config = WorkerConfig(worker_id="worker-start-test", device_id="emulator-5554", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[CheckLoginHandler()],
    )

    with caplog.at_level(logging.INFO, logger="boss_agent.worker"):
        start_task = asyncio.create_task(worker.start())
        await asyncio.sleep(0.02)
        worker.stop()
        await start_task

    log_records = [r.message for r in caplog.records if r.name == "boss_agent.worker"]
    # Must log readiness with device ID and polling interval
    assert any("loop started" in msg.lower() and "emulator-5554" in msg for msg in log_records)


def test_format_payload_for_logging_truncation():
    """Verify _format_payload_for_logging compresses large docs, truncates long strings and lists."""
    from boss_agent.worker.daemon import _format_payload_for_logging

    huge_doc = "# Candidate Profile\n" + ("x" * 2000)
    huge_payload = {
        "auto_send": False,
        "candidate_profile": {
            "name": "周黄金",
            "profile_document": huge_doc,
            "core_skills": [f"Skill-{i}" for i in range(12)],
            "long_desc": "a" * 300,
        },
    }

    formatted = _format_payload_for_logging(huge_payload, max_str_len=50, max_list_items=3)

    # Document should be summarized with char count
    doc_val = formatted["candidate_profile"]["profile_document"]
    assert "chars: # Candidate Profile" in doc_val
    assert len(doc_val) < 100

    # Skills list truncated to 3 + more items label
    skills = formatted["candidate_profile"]["core_skills"]
    assert len(skills) == 4
    assert "+9 more items" in skills[-1]

    # Long string truncated
    long_desc = formatted["candidate_profile"]["long_desc"]
    assert "(total 300 chars)" in long_desc
    assert len(long_desc) < 80


@pytest.mark.asyncio
async def test_worker_logs_truncated_payload_on_task_claim(broker, mock_driver, caplog):
    """Verify claimed task logs output truncated payload instead of raw massive blobs."""
    import logging

    config = WorkerConfig(worker_id="worker-trunc-test", device_id="emulator-5554", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[CheckLoginHandler()],
    )

    huge_payload = {
        "auto_send": False,
        "candidate_profile": {
            "name": "周黄金",
            "profile_document": "# Profile Document\n" + ("y" * 3000),
            "core_skills": [f"Skill-{i}" for i in range(15)],
        },
    }

    await broker.create_task(
        task_type=TaskType.CHECK_LOGIN,
        payload=huge_payload,
    )

    with caplog.at_level(logging.INFO, logger="boss_agent.worker"):
        await worker.run_once()

    claim_logs = [
        r.message for r in caplog.records
        if r.name == "boss_agent.worker" and "Claimed task" in r.message
    ]
    assert len(claim_logs) == 1
    log_msg = claim_logs[0]

    # Full raw 3000-char text must NOT be in the log line
    assert ("y" * 50) not in log_msg
    # Summarized marker must be present
    assert "chars: # Profile Document" in log_msg
    assert "+10 more items" in log_msg


async def _delete_task_record(broker, task_id: str) -> None:
    """Simulate an external purge of the task record while the worker is mid-execution."""
    async with broker._lock:
        broker._tasks.pop(task_id, None)


@pytest.mark.asyncio
async def test_worker_survives_task_record_vanishing_before_finalize(broker, mock_driver, caplog):
    """A record deleted during execution must not crash the worker on failure finalization.

    PocketBase answers the outcome PATCH with 404 and InMemory raises KeyError once the
    record is gone; finalize must treat the vanished record as terminal and move on.
    """
    import logging

    from boss_agent.worker.handlers.base import BaseTaskHandler, HandlerResult

    class VanishingFailureHandler(BaseTaskHandler):
        @property
        def task_type(self) -> TaskType:
            return TaskType.SCRAPE_JOBS

        async def handle(self, task, broker, context) -> HandlerResult:
            await _delete_task_record(broker, task.id)
            return HandlerResult(success=False, error_message="simulated failure")

    config = WorkerConfig(worker_id="worker-vanish-finalize", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[VanishingFailureHandler()],
    )

    task = await broker.create_task(task_type=TaskType.SCRAPE_JOBS)

    with caplog.at_level(logging.WARNING, logger="boss_agent.worker"):
        executed = await worker.run_once()

    assert executed is True
    log_records = [r.message for r in caplog.records if r.name == "boss_agent.worker"]
    assert any(task.id in msg and "no longer exists" in msg for msg in log_records)


@pytest.mark.asyncio
async def test_worker_survives_uncaught_exception_with_vanished_record(broker, mock_driver, caplog):
    """An uncaught handler exception must not escalate when the record was already purged."""
    import logging

    from boss_agent.worker.handlers.base import BaseTaskHandler, HandlerResult

    class VanishingThrowingHandler(BaseTaskHandler):
        @property
        def task_type(self) -> TaskType:
            return TaskType.SCRAPE_JOBS

        async def handle(self, task, broker, context) -> HandlerResult:
            await _delete_task_record(broker, task.id)
            raise RuntimeError("simulated crash")

    config = WorkerConfig(worker_id="worker-vanish-exception", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=mock_driver)
    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[VanishingThrowingHandler()],
    )

    task = await broker.create_task(task_type=TaskType.SCRAPE_JOBS)

    with caplog.at_level(logging.WARNING, logger="boss_agent.worker"):
        executed = await worker.run_once()

    assert executed is True
    log_records = [r.message for r in caplog.records if r.name == "boss_agent.worker"]
    assert any(task.id in msg and "no longer exists" in msg for msg in log_records)





