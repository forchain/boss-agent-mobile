"""
tests/unit/test_lease_sweeper.py
================================
Unit tests for Worker Crash Recovery & Orphan Task Lease Sweeper (Issue #32).
"""

from datetime import UTC, datetime, timedelta

import pytest

from boss_agent.broker.models import TaskStatus, TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.broker.sweeper import TaskLeaseSweeper


@pytest.fixture
def broker():
    return InMemoryTaskBroker()


@pytest.mark.asyncio
async def test_sweeper_requeues_crashed_worker_task_if_under_retry_limit(broker):
    """Verify stale running task is re-queued as PENDING with incremented retry count."""
    task = await broker.create_task(task_type=TaskType.CHECK_LOGIN)
    claimed = await broker.claim_task(task.id, worker_id="crashed-worker-1")
    assert claimed is not None
    assert claimed.status == TaskStatus.RUNNING

    # Manually simulate stale heartbeat older than 60 seconds
    past_time = datetime.now(UTC) - timedelta(seconds=120)
    broker._tasks[task.id].last_heartbeat_at = past_time

    sweeper = TaskLeaseSweeper(broker=broker, lease_timeout_sec=60.0, retry_limit=2)
    recovered_ids = await sweeper.sweep_stale_tasks()

    assert task.id in recovered_ids
    recovered_task = await broker.get_task(task.id)
    assert recovered_task is not None
    assert recovered_task.status == TaskStatus.PENDING
    assert recovered_task.worker_id is None
    assert recovered_task.retry_count == 1
    assert any("re-queuing" in log.lower() for log in recovered_task.logs)


@pytest.mark.asyncio
async def test_sweeper_fails_crashed_worker_task_when_max_retries_exceeded(broker):
    """Verify stale running task is marked FAILED when max retries exceeded."""
    task = await broker.create_task(task_type=TaskType.AUTO_APPLY)
    claimed = await broker.claim_task(task.id, worker_id="crashed-worker-2")
    assert claimed is not None

    # Simulate task already retried twice
    past_time = datetime.now(UTC) - timedelta(seconds=120)
    broker._tasks[task.id].last_heartbeat_at = past_time
    broker._tasks[task.id].retry_count = 2

    sweeper = TaskLeaseSweeper(broker=broker, lease_timeout_sec=60.0, retry_limit=2)
    recovered_ids = await sweeper.sweep_stale_tasks()

    assert task.id in recovered_ids
    failed_task = await broker.get_task(task.id)
    assert failed_task is not None
    assert failed_task.status == TaskStatus.FAILED
    assert failed_task.error_message is not None
    assert "lease expired" in failed_task.error_message.lower()
    assert any("worker crash" in log.lower() for log in failed_task.logs)


@pytest.mark.asyncio
async def test_sweeper_ignores_active_running_tasks_with_fresh_heartbeat(broker):
    """Verify running tasks with recent heartbeats are not touched by sweeper."""
    task = await broker.create_task(task_type=TaskType.SCRAPE_JOBS)
    claimed = await broker.claim_task(task.id, worker_id="healthy-worker-3")
    assert claimed is not None

    # Heartbeat is current (now)
    sweeper = TaskLeaseSweeper(broker=broker, lease_timeout_sec=60.0, retry_limit=2)
    recovered_ids = await sweeper.sweep_stale_tasks()

    assert len(recovered_ids) == 0
    running_task = await broker.get_task(task.id)
    assert running_task is not None
    assert running_task.status == TaskStatus.RUNNING
    assert running_task.worker_id == "healthy-worker-3"
