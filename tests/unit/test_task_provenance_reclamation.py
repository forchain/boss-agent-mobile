"""
tests/unit/test_task_provenance_reclamation.py
==============================================
Task Provenance as a real attribute, and the rule CONTEXT.md attaches to it.

Provenance used to exist nowhere in code — the startup barrier and the scheduler faked
it with payload markers (`startup_cleanup`, `scheduled`). It is now a column on the
task record, carried by the launch builders, and the worker's startup sweep acts on it:
a `test`-sourced task is cancelled so an automated suite never contends with the live
worker for the device.
"""

import pytest

from boss_agent.broker.models import TaskStatus
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.models import FilterConfig, SavedSearch, SearchConfig
from boss_agent.task_launch import LaunchSource, TaskKind, build_launch
from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.daemon import TEST_RECLAIM_REASON, AutomationWorker


async def _queue(broker: InMemoryTaskBroker, search_id: str, source: LaunchSource):
    search = SavedSearch(
        id=search_id,
        name=f"策略 {search_id}",
        search=SearchConfig(keyword="agent"),
        filter=FilterConfig(),
        target_action="save_jd",
        target_task_type="SCRAPE_JOBS",
    )
    launch = build_launch(TaskKind.SEARCH, source=source, search=search)
    return await broker.create_task(
        task_type=launch.task_type, payload=launch.payload, source=launch.source.value
    )


def _worker(broker: InMemoryTaskBroker) -> AutomationWorker:
    return AutomationWorker(
        broker=broker,
        config=WorkerConfig(worker_id="test-worker", run_cleanup_on_startup=False),
    )


@pytest.mark.asyncio
async def test_a_launch_records_its_provenance_on_the_task() -> None:
    broker = InMemoryTaskBroker()
    task = await _queue(broker, "s1", LaunchSource.SCHEDULER)
    assert task.source == "scheduler"
    # Not a payload key: the record carries it.
    assert "source" not in task.payload


@pytest.mark.asyncio
async def test_startup_cancels_test_sourced_tasks_and_leaves_the_rest() -> None:
    broker = InMemoryTaskBroker()
    test_task = await _queue(broker, "s_test", LaunchSource.TEST)
    manual_task = await _queue(broker, "s_manual", LaunchSource.MANUAL)
    scheduled_task = await _queue(broker, "s_cron", LaunchSource.SCHEDULER)

    worker = _worker(broker)
    reclaimed = await worker._reclaim_test_sourced_tasks()

    assert reclaimed == [test_task.id]

    assert (await broker.get_task(test_task.id)).status == TaskStatus.CANCELLED
    for survivor in (manual_task, scheduled_task):
        assert (await broker.get_task(survivor.id)).status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_the_reclamation_is_legible_in_the_task_log() -> None:
    """An operator reading the task's log learns why it never ran."""
    broker = InMemoryTaskBroker()
    task = await _queue(broker, "s_test", LaunchSource.TEST)

    await _worker(broker)._reclaim_test_sourced_tasks()

    cancelled = await broker.get_task(task.id)
    assert TEST_RECLAIM_REASON in " ".join(cancelled.logs)
    assert cancelled.error_message == TEST_RECLAIM_REASON


@pytest.mark.asyncio
async def test_a_task_without_provenance_is_left_alone() -> None:
    """A record predating the column reads as manual: the sweep never reclaims what it
    cannot attribute."""
    broker = InMemoryTaskBroker()
    task = await broker.create_task(task_type="SCRAPE_JOBS", payload={"keyword": "agent"})
    task.source = ""

    reclaimed = await _worker(broker)._reclaim_test_sourced_tasks()

    assert reclaimed == []
    assert (await broker.get_task(task.id)).status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_the_sweep_tolerates_a_broker_that_cannot_be_scanned() -> None:
    """A startup sweep that throws would take the worker down with it."""

    class BrokenBroker(InMemoryTaskBroker):
        async def list_pending_tasks(self, limit: int = 10):  # type: ignore[override]
            raise RuntimeError("broker unavailable")

    assert await _worker(BrokenBroker())._reclaim_test_sourced_tasks() == []
