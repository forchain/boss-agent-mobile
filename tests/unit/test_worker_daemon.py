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
