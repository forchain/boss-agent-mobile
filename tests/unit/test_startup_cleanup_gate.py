"""
tests.unit.test_startup_cleanup_gate
====================================
Unit tests for the startup 拒信清扫 barrier (issue #230): a service that starts up
queues one CHECK_CHAT and holds every search task back until it reaches a terminal
state, so the companies that already rejected the candidate are in the company
blacklist before any job search or greeting is dispatched.
"""

from unittest.mock import MagicMock

import pytest

from boss_agent.broker.models import TaskStatus, TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.startup_cleanup import STARTUP_CLEANUP_MARKER, StartupCleanupGate
from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.daemon import AutomationWorker
from boss_agent.worker.handlers.base import BaseTaskHandler, HandlerResult

TERMINAL_STATUSES = (TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.CANCELLED)


@pytest.fixture
def broker():
    return InMemoryTaskBroker()


@pytest.fixture
def counting_broker():
    return CountingBroker()


class CountingBroker(InMemoryTaskBroker):
    """Broker that counts how many times the pending queue was read."""

    def __init__(self) -> None:
        super().__init__()
        self.pending_reads = 0

    async def list_pending_tasks(self, limit: int = 10):
        self.pending_reads += 1
        return await super().list_pending_tasks(limit=limit)


class EarlyRacerBroker(InMemoryTaskBroker):
    """Models a sibling service whose cleanup lands between our read and our write.

    The worker daemon and the scheduler arm concurrently (``asyncio.gather``, or two
    processes), so both can read an empty queue before either write is visible.
    """

    def __init__(self) -> None:
        super().__init__()
        self._injected = False

    async def create_task(self, task_type, payload=None, source="manual"):
        if not self._injected and payload and payload.get(STARTUP_CLEANUP_MARKER):
            self._injected = True
            await super().create_task(
                TaskType.CHECK_CHAT, {STARTUP_CLEANUP_MARKER: True, "trigger": "startup"}
            )
        return await super().create_task(task_type, payload)


class LateRacerBroker(InMemoryTaskBroker):
    """The mirror race: the sibling's cleanup lands just after ours."""

    def __init__(self) -> None:
        super().__init__()
        self._injected = False

    async def create_task(self, task_type, payload=None, source="manual"):
        mine = await super().create_task(task_type, payload)
        if not self._injected and payload and payload.get(STARTUP_CLEANUP_MARKER):
            self._injected = True
            await super().create_task(TaskType.CHECK_CHAT, {STARTUP_CLEANUP_MARKER: True})
        return mine


class RecordingHandler(BaseTaskHandler):
    """Handler that records the order in which task types were executed."""

    def __init__(self, task_type: TaskType, executed: list[TaskType], *, succeed: bool = True):
        self._task_type = task_type
        self._executed = executed
        self._succeed = succeed

    @property
    def task_type(self) -> TaskType:
        return self._task_type

    async def handle(self, task, broker, context) -> HandlerResult:
        self._executed.append(task.task_type)
        if self._succeed:
            return HandlerResult(success=True)
        return HandlerResult(success=False, error_message="模拟拒信清扫执行失败")


def build_worker(broker, executed: list[TaskType], *, enabled: bool, cleanup_succeeds: bool = True):
    config = WorkerConfig(
        worker_id="worker-startup-gate",
        device_id="emulator-5554",
        poll_interval_sec=0.01,
        run_cleanup_on_startup=enabled,
    )
    return AutomationWorker(
        config=config,
        broker=broker,
        context=WorkerContext(config=config, driver=MagicMock()),
        handlers=[
            RecordingHandler(TaskType.CHECK_CHAT, executed, succeed=cleanup_succeeds),
            RecordingHandler(TaskType.SCRAPE_JOBS, executed),
            RecordingHandler(TaskType.AUTO_APPLY, executed),
        ],
    )


# ---------------------------------------------------------------------------
# The barrier itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_gate_queues_nothing_and_never_blocks(broker):
    gate = StartupCleanupGate(broker, enabled=False)

    assert await gate.arm() is None
    assert await broker.list_pending_tasks() == []
    assert await gate.held_types() == frozenset()


@pytest.mark.asyncio
async def test_enabled_gate_queues_a_marked_check_chat_task(broker):
    gate = StartupCleanupGate(broker)

    task = await gate.arm()

    assert task is not None
    assert task.task_type is TaskType.CHECK_CHAT
    assert task.payload[STARTUP_CLEANUP_MARKER] is True
    # The triage settings travel with the task, so the startup run honours the
    # configured drill mode and reply text instead of falling back to defaults.
    assert {"rejection_reply_text", "max_scan_depth", "dry_run"} <= set(task.payload)


@pytest.mark.asyncio
async def test_only_search_tasks_are_held_back(broker):
    gate = StartupCleanupGate(broker)
    await gate.arm()

    held = await gate.held_types()

    assert held == {TaskType.SCRAPE_JOBS, TaskType.AUTO_APPLY}
    assert TaskType.CHECK_CHAT not in held
    assert TaskType.CHECK_LOGIN not in held


@pytest.mark.asyncio
async def test_the_barrier_costs_one_queue_read_per_claim_attempt(counting_broker):
    """The worker asks once per poll, not once per pending task (issue #230)."""
    gate = StartupCleanupGate(counting_broker)
    await gate.arm()
    counting_broker.pending_reads = 0

    held = await gate.held_types()

    assert held == {TaskType.SCRAPE_JOBS, TaskType.AUTO_APPLY}
    assert counting_broker.pending_reads == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", TERMINAL_STATUSES)
async def test_barrier_releases_when_the_cleanup_reaches_a_terminal_state(broker, status):
    gate = StartupCleanupGate(broker)
    task = await gate.arm()
    assert task is not None

    await broker.update_task_status(task.id, status=status)

    assert await gate.held_types() == frozenset()


@pytest.mark.asyncio
async def test_arming_is_once_only_and_dedupes_across_processes(broker):
    gate = StartupCleanupGate(broker)
    assert await gate.arm() is not None

    # The scheduler runs beside the worker in the same deployment: it must see the
    # live barrier instead of queueing a second cleanup, and must not re-queue one
    # on every poll.
    sibling = StartupCleanupGate(broker)
    assert await sibling.arm() is None
    assert await sibling.arm() is None

    assert len(await broker.list_pending_tasks()) == 1
    assert await sibling.held_types() == {TaskType.SCRAPE_JOBS, TaskType.AUTO_APPLY}


@pytest.mark.asyncio
async def test_a_racing_arm_stands_down_for_the_cleanup_that_landed_first():
    """Two services arming at once must leave exactly one cleanup queued, not two."""
    broker = EarlyRacerBroker()
    gate = StartupCleanupGate(broker)

    task = await gate.arm()

    assert task is None, "the losing racer must not report itself as the queued cleanup"
    pending = await broker.list_pending_tasks()
    assert [t.task_type for t in pending] == [TaskType.CHECK_CHAT]
    assert pending[0].payload[STARTUP_CLEANUP_MARKER] is True


@pytest.mark.asyncio
async def test_a_racing_arm_cancels_the_duplicate_that_landed_second():
    """Whichever way the two re-reads interleave, the queue must converge on one."""
    broker = LateRacerBroker()
    gate = StartupCleanupGate(broker)

    task = await gate.arm()

    assert task is not None
    pending = await broker.list_pending_tasks()
    assert [t.id for t in pending] == [task.id]
    assert await gate.held_types() == {TaskType.SCRAPE_JOBS, TaskType.AUTO_APPLY}


@pytest.mark.asyncio
async def test_startup_cleanup_jumps_ahead_of_queued_search_tasks(broker):
    """The queue is ordered by creation, so a stale pending search would otherwise win."""
    stale = await broker.create_task(TaskType.SCRAPE_JOBS, {"keyword": "stale"})
    gate = StartupCleanupGate(broker)
    cleanup = await gate.arm()
    assert cleanup is not None

    ordered = gate.prioritize(await broker.list_pending_tasks())

    assert [task.id for task in ordered] == [cleanup.id, stale.id]


# ---------------------------------------------------------------------------
# Worker integration: startup ordering, gating, release
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_runs_the_startup_cleanup_before_a_pending_search_task(broker):
    executed: list[TaskType] = []
    worker = build_worker(broker, executed, enabled=True)
    await broker.create_task(TaskType.SCRAPE_JOBS, {"keyword": "queued before startup"})

    await worker.start(max_runs=2)

    assert executed == [TaskType.CHECK_CHAT, TaskType.SCRAPE_JOBS]


@pytest.mark.asyncio
async def test_search_task_stays_pending_until_the_cleanup_settles(broker):
    executed: list[TaskType] = []
    worker = build_worker(broker, executed, enabled=True)
    waiting = await broker.create_task(TaskType.AUTO_APPLY, {"keyword": "held back"})

    await worker.start(max_runs=1)

    assert executed == [TaskType.CHECK_CHAT]
    held = await broker.get_task(waiting.id)
    assert held is not None
    assert held.status is TaskStatus.PENDING, "the search must not run ahead of the cleanup"


@pytest.mark.asyncio
async def test_disabled_setting_lets_search_tasks_run_without_a_cleanup(broker):
    executed: list[TaskType] = []
    worker = build_worker(broker, executed, enabled=False)
    await broker.create_task(TaskType.SCRAPE_JOBS, {"keyword": "no barrier"})

    await worker.start(max_runs=1)

    assert executed == [TaskType.SCRAPE_JOBS]
    assert all(
        task.task_type is not TaskType.CHECK_CHAT for task in await broker.list_pending_tasks()
    )


@pytest.mark.asyncio
async def test_a_failed_cleanup_releases_the_barrier_instead_of_deadlocking(broker):
    executed: list[TaskType] = []
    worker = build_worker(broker, executed, enabled=True, cleanup_succeeds=False)
    await broker.create_task(TaskType.SCRAPE_JOBS, {"keyword": "after the failure"})

    await worker.start(max_runs=2)

    assert executed == [TaskType.CHECK_CHAT, TaskType.SCRAPE_JOBS]
