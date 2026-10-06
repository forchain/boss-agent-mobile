"""
tests/unit/test_orphan_task_recovery.py
=======================================
Regression tests for the orphan-task defect behind a permanently "RUNNING" dashboard.

A worker can die mid-task -- here the process lost its Python environment and every
PocketBase call began failing, so the outcome that would have moved the task to SUCCESS
or FAILED never reached the broker. The row kept ``status='running'`` with a heartbeat
that stopped advancing, and no component ever reconciled it, so the dashboard reported
"RUNNING" for a task that had not executed in 31 hours.

These tests pin the two halves of the fix:

1. the worker supervises a lease sweeper, so an orphan is reclaimed (re-queued, then
   failed once retries are exhausted) instead of lingering forever; and
2. the lease outlives the worst-case event-loop stall, so a *live* task blocked in a
   slow Appium command is never mistaken for an orphan and re-run.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from boss_agent.broker.models import TaskStatus, TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker, PocketBaseTaskBroker
from boss_agent.broker.sweeper import TaskLeaseSweeper
from boss_agent.worker.config import WorkerConfig

#: The Appium server drops a session after this long without a command. A handler can
#: block the event loop for up to one of these inside a single synchronous driver call.
APPIUM_NEW_COMMAND_TIMEOUT_SEC = 300.0

#: Upper bound on how long this test waits for a background sweep to observe a change.
SWEEP_BUDGET_SEC = 5.0


async def _wait_until(predicate, timeout: float = SWEEP_BUDGET_SEC) -> bool:
    """Poll `predicate` until it holds, so tests never depend on a fixed sleep."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


def _age_heartbeat(broker: InMemoryTaskBroker, task_id: str, seconds: float) -> None:
    """Backdate a task's lease to simulate a worker that stopped renewing it."""
    broker._tasks[task_id].last_heartbeat_at = datetime.now(UTC) - timedelta(seconds=seconds)


@pytest.mark.asyncio
async def test_worker_config_lease_outlives_the_worst_case_event_loop_stall():
    """A live task blocked in Appium must not be reaped as an orphan.

    Page objects are synchronous and are called inline from `async` pipeline methods, so
    one slow Appium command can stall the event loop -- and with it the heartbeat
    coroutine -- for up to `new_command_timeout`. If the lease were shorter than that,
    the sweeper would re-queue a task that is still being worked on, and AUTO_APPLY
    would run twice against the same feed.
    """
    config = WorkerConfig()

    assert config.lease_timeout_sec > APPIUM_NEW_COMMAND_TIMEOUT_SEC, (
        f"lease_timeout_sec={config.lease_timeout_sec}s does not exceed the Appium "
        f"command timeout of {APPIUM_NEW_COMMAND_TIMEOUT_SEC}s; a task blocked in a "
        "synchronous driver call would be reaped while still running"
    )
    assert config.lease_timeout_sec > config.heartbeat_interval_sec


@pytest.mark.asyncio
async def test_worker_lease_is_not_shorter_than_the_heartbeat_renewal_interval():
    """Several heartbeats must fit inside one lease, or a live task flaps as stale.

    With a single heartbeat per lease a transient scheduling delay would expire the
    lease between two renewals, so a healthy task would be re-queued.
    """
    config = WorkerConfig()

    assert config.lease_timeout_sec >= config.heartbeat_interval_sec * 3


@pytest.mark.asyncio
async def test_supervised_lease_sweeper_reclaims_a_crashed_workers_orphan():
    """An orphan left in `running` is re-queued by the supervised sweeper.

    This is the defect itself: the task's worker vanished, its heartbeat froze, and
    nothing reconciled the row, so the dashboard showed a permanently running task.
    """
    config = WorkerConfig(lease_timeout_sec=0.2)
    broker = InMemoryTaskBroker()

    task = await broker.create_task(task_type=TaskType.AUTO_APPLY)
    claimed = await broker.claim_task(task.id, worker_id="crashed-worker")
    assert claimed is not None
    _age_heartbeat(broker, task.id, config.lease_timeout_sec * 10)

    sweeper = TaskLeaseSweeper(
        broker=broker,
        lease_timeout_sec=config.lease_timeout_sec,
        retry_limit=config.max_task_retries,
    )
    sweep_task = asyncio.create_task(sweeper.start(interval_sec=0.05))
    try:
        assert await _wait_until(lambda: broker._tasks[task.id].status != TaskStatus.RUNNING), (
            "orphan task was never reclaimed; it would stay RUNNING on the dashboard"
        )
    finally:
        sweeper.stop()
        sweep_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await sweep_task

    recovered = await broker.get_task(task.id)
    assert recovered.status == TaskStatus.PENDING
    assert recovered.worker_id is None
    assert recovered.retry_count == 1
    assert any("lease expired" in line.lower() for line in recovered.logs)


@pytest.mark.asyncio
async def test_supervised_lease_sweeper_fails_an_orphan_once_retries_are_exhausted():
    """An orphan that keeps failing stops being re-queued and is failed terminally.

    Re-queuing forever would leave the dashboard showing an active task indefinitely,
    so the retry limit has to end in a terminal state.
    """
    config = WorkerConfig(lease_timeout_sec=0.2)
    broker = InMemoryTaskBroker()

    task = await broker.create_task(task_type=TaskType.AUTO_APPLY)
    claimed = await broker.claim_task(task.id, worker_id="crashed-worker")
    assert claimed is not None
    broker._tasks[task.id].retry_count = config.max_task_retries
    _age_heartbeat(broker, task.id, config.lease_timeout_sec * 10)

    sweeper = TaskLeaseSweeper(
        broker=broker,
        lease_timeout_sec=config.lease_timeout_sec,
        retry_limit=config.max_task_retries,
    )
    sweep_task = asyncio.create_task(sweeper.start(interval_sec=0.05))
    try:
        assert await _wait_until(lambda: broker._tasks[task.id].status == TaskStatus.FAILED), (
            "orphan past its retry limit never reached a terminal state"
        )
    finally:
        sweeper.stop()
        sweep_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await sweep_task

    failed = await broker.get_task(task.id)
    assert failed.status.is_terminal()
    assert "lease expired" in (failed.error_message or "").lower()


@pytest.mark.asyncio
async def test_supervised_lease_sweeper_leaves_a_heartbeating_task_alone():
    """The sweeper must never touch the task its own worker is actively running.

    This is the false-reap guard. Re-queuing a live AUTO_APPLY would dispatch a second
    round of greetings to employers the run has already contacted.
    """
    config = WorkerConfig(lease_timeout_sec=60.0)
    broker = InMemoryTaskBroker()

    task = await broker.create_task(task_type=TaskType.AUTO_APPLY)
    claimed = await broker.claim_task(task.id, worker_id="live-worker")
    assert claimed is not None

    sweeper = TaskLeaseSweeper(
        broker=broker,
        lease_timeout_sec=config.lease_timeout_sec,
        retry_limit=config.max_task_retries,
    )
    recovered = await sweeper.sweep_stale_tasks()

    assert recovered == []
    still_running = await broker.get_task(task.id)
    assert still_running.status == TaskStatus.RUNNING
    assert still_running.worker_id == "live-worker"
    assert still_running.retry_count == 0


@pytest.mark.asyncio
async def test_pocketbase_records_round_trip_the_retry_count():
    """`retry_count` must survive the broker read, or recovery never terminates.

    `retry_count` is a real column and `requeue_task` writes it, but the PocketBase
    record decoder dropped it, so every task read back from the broker reported the
    model default of 0. The sweeper compares that count against its retry limit, so a
    persisted orphan would be re-queued forever and never reach a terminal state --
    exactly the permanently-active dashboard state this suite exists to prevent. The
    in-memory broker sets the field, so only a record-decoding test can catch it.
    """
    broker = PocketBaseTaskBroker(base_url="http://127.0.0.1:8090")
    record = {
        "id": "abcdefghij12345",
        "task_type": TaskType.AUTO_APPLY.value,
        "status": TaskStatus.PENDING.value,
        "payload": {},
        "logs": [],
        "retry_count": 2,
        "max_retries": 2,
    }

    decoded = broker._record_to_task(record)

    assert decoded.retry_count == 2, (
        "retry_count is not deserialized from the broker record; lease recovery would "
        "re-queue an orphan indefinitely instead of failing it"
    )


@pytest.mark.asyncio
async def test_worker_entrypoint_supervises_the_lease_sweeper():
    """The worker must actually run a sweeper, or orphans are never reclaimed.

    `TaskLeaseSweeper` shipped fully implemented and unit-tested but had no production
    caller: `scripts/worker.py` built its service list from the worker loop and the
    scheduler only. This asserts the wiring exists, so the recovery path cannot be
    dropped again without a test going red.
    """
    from scripts import worker as worker_entrypoint

    from boss_agent.broker.sweeper import TaskLeaseSweeper as _Sweeper

    supervised = worker_entrypoint.build_supervised_services(
        config=WorkerConfig(enable_scheduler=False),
        broker=InMemoryTaskBroker(),
    )

    try:
        assert any(coro.cr_code.co_name == _Sweeper.start.__name__ for coro in supervised), (
            "no TaskLeaseSweeper.start() coroutine is supervised by the worker entrypoint; "
            "orphan running tasks will never be reclaimed"
        )
    finally:
        # Never supervised here, so close them rather than leaking "was never awaited".
        for coro in supervised:
            coro.close()
