"""
tests/unit/test_takeover_state_machine.py
=========================================
Unit and integration tests for Human-in-the-Loop Takeover State Machine (Issue #31).
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from boss_agent.broker.models import TaskStatus, TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.daemon import AutomationWorker
from boss_agent.worker.handlers.base import BaseTaskHandler, HandlerResult


class MockTakeoverChallengeHandler(BaseTaskHandler):
    """Simulates a task handler that hits a captcha challenge and pauses for takeover."""

    def __init__(self, should_resolve_on_resume: bool = True) -> None:
        self.should_resolve_on_resume = should_resolve_on_resume

    @property
    def task_type(self) -> TaskType:
        return TaskType.AUTO_APPLY

    async def handle(self, task, broker, context) -> HandlerResult:
        await broker.append_log(task.id, "Encountered slider captcha challenge")
        await broker.update_task_status(
            task.id,
            status=TaskStatus.PAUSED_FOR_TAKEOVER,
            error_message="Slider captcha detected. Manual takeover required.",
        )

        # Wait for resume signal
        for _ in range(50):
            await asyncio.sleep(0.05)
            current_task = await broker.get_task(task.id)
            if not current_task:
                return HandlerResult(success=False, error_message="Task disappeared")
            if current_task.status in (TaskStatus.RESUMING, TaskStatus.RUNNING):
                await broker.append_log(
                    task.id, "Resume signal received. Verifying challenge solved..."
                )
                if self.should_resolve_on_resume:
                    await broker.append_log(
                        task.id, "Challenge solved! Resuming application workflow..."
                    )
                    return HandlerResult(success=True)
            if current_task.status == TaskStatus.CANCELLED:
                return HandlerResult(success=False, error_message="Task cancelled during takeover")

        return HandlerResult(success=False, error_message="Takeover timed out")


@pytest.fixture
def broker():
    return InMemoryTaskBroker()


@pytest.mark.asyncio
async def test_takeover_pause_and_resume_end_to_end(broker):
    """Verify worker pauses on challenge, and resumes when resume signal is sent to broker."""
    driver = MagicMock()
    config = WorkerConfig(worker_id="test-takeover-worker", poll_interval_sec=0.01)
    context = WorkerContext(config=config, driver=driver)

    handler = MockTakeoverChallengeHandler(should_resolve_on_resume=True)
    worker = AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[handler],
    )

    task = await broker.create_task(task_type=TaskType.AUTO_APPLY, payload={})

    # Start worker execution in background task
    worker_task = asyncio.create_task(worker.run_once())

    # Wait for task to enter PAUSED_FOR_TAKEOVER
    for _ in range(30):
        await asyncio.sleep(0.05)
        t = await broker.get_task(task.id)
        if t and t.status == TaskStatus.PAUSED_FOR_TAKEOVER:
            break

    paused_task = await broker.get_task(task.id)
    assert paused_task is not None
    assert paused_task.status == TaskStatus.PAUSED_FOR_TAKEOVER

    # User clicks Resume in SvelteKit UI -> updates status to RESUMING in PocketBase Broker
    await broker.update_task_status(task.id, status=TaskStatus.RESUMING)

    # Wait for worker to finish
    await worker_task

    finished_task = await broker.get_task(task.id)
    assert finished_task is not None
    assert finished_task.status == TaskStatus.SUCCESS
    assert any("resuming" in log.lower() for log in finished_task.logs)

