"""
src/boss_agent/startup_cleanup.py
=================================
Startup 拒信清扫 barrier (issue #230).

A service that starts up ("开服") queues one CHECK_CHAT before anything else and
holds every search task back until it settles. Employers that already rejected the
candidate are thereby ingested into `company_blacklist` before the first job search
or greeting is dispatched, instead of being re-applied to because the blacklist was
still empty when the search ran.

The barrier is derived from the *pending queue* rather than from the task id this
process created, so a cleanup queued by the scheduler is as binding as one queued by
the worker daemon -- the two run as separate services (and, with `--enable-scheduler`,
in one process).
"""

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from boss_agent.broker.models import AutomationTask, TaskStatus, TaskType
from boss_agent.broker.pocketbase_adapter import BaseTaskBroker
from boss_agent.settings import resolve_chat_acknowledgment_settings

logger = logging.getLogger("boss_agent.startup_cleanup")

#: Payload marker identifying the cleanup queued by a service startup. A marker is
#: used rather than a task id because the queue outlives the process that filled it.
STARTUP_CLEANUP_MARKER: str = "startup_cleanup"

#: Task types that must not run before the startup cleanup settles.
SEARCH_TASK_TYPES: frozenset[TaskType] = frozenset({TaskType.SCRAPE_JOBS, TaskType.AUTO_APPLY})

#: Pending-queue window the gate inspects. The queue is ordered by creation, so the
#: startup cleanup sits behind anything already pending -- a window (rather than a
#: single head-of-queue read) is what keeps that from hiding it.
QUEUE_SCAN_LIMIT: int = 50


def _startup_cleanup_payload() -> dict[str, Any]:
    """Payload for the startup cleanup: the marker plus the resolved triage settings.

    The settings travel with the task so a startup run honours the configured drill
    mode and reply text instead of falling back to the code defaults. `trigger` is
    provenance for whoever later inspects the queue record.
    """
    ack = resolve_chat_acknowledgment_settings()
    return {
        STARTUP_CLEANUP_MARKER: True,
        "trigger": "startup",
        "dry_run": ack.dry_run,
        "rejection_reply_text": ack.rejection_reply_text,
        "max_scan_depth": ack.max_scan_depth,
    }


class StartupCleanupGate:
    """Queues the startup 拒信清扫 and holds search tasks until it settles."""

    def __init__(
        self,
        broker: BaseTaskBroker,
        *,
        enabled: bool = True,
        queue_scan_limit: int = QUEUE_SCAN_LIMIT,
    ) -> None:
        self.broker = broker
        self.enabled = enabled
        self.queue_scan_limit = queue_scan_limit
        self._armed = False
        self._arm_lock = asyncio.Lock()

    @staticmethod
    def is_startup_cleanup(task: AutomationTask) -> bool:
        """Whether this task is the cleanup a startup queued."""
        return task.task_type is TaskType.CHECK_CHAT and bool(
            (task.payload or {}).get(STARTUP_CLEANUP_MARKER)
        )

    def prioritize(self, tasks: Sequence[AutomationTask]) -> list[AutomationTask]:
        """Startup cleanups first, every other task in its original queue order.

        The queue is ordered by creation time, so a task left pending by an earlier
        run would otherwise be claimed ahead of the cleanup and the barrier would
        only be discovered one poll later.
        """
        return sorted(tasks, key=lambda task: 0 if self.is_startup_cleanup(task) else 1)

    async def arm(self) -> AutomationTask | None:
        """Queue the startup cleanup once per process, unless one is already queued.

        Returns the surviving task, or None when the gate is disabled or another
        service (e.g. the scheduler beside this worker) already queued one.
        """
        if not self.enabled or self._armed:
            return None
        self._armed = True

        try:
            async with self._arm_lock:
                if await self._pending_cleanups():
                    logger.info("Startup 拒信清扫 already queued; not queueing a second one")
                    return None
                task = await self.broker.create_task(
                    task_type=TaskType.CHECK_CHAT, payload=_startup_cleanup_payload()
                )

            return await self._settle_race(task)
        except Exception as e:
            logger.warning("Could not arm startup 拒信清扫 barrier: %s", e)
            return None

    async def held_types(self) -> frozenset[TaskType]:
        """Every task type that must wait for the startup cleanup to settle.

        Resolved in one queue read so a claim pass over a long pending queue costs a
        single query rather than one per task.
        """
        if not self.enabled:
            return frozenset()
        try:
            if not await self._pending_cleanups():
                return frozenset()
        except Exception as e:
            logger.warning("Could not check pending cleanups for startup barrier: %s", e)
            return frozenset()
        return SEARCH_TASK_TYPES

    async def _settle_race(self, task: AutomationTask) -> AutomationTask | None:
        """Collapse concurrent arms down to a single startup cleanup.

        The arm lock only serialises one process; two services (or two processes)
        can still both read an empty queue before either write is visible. The oldest
        marked cleanup wins, and every racer cancels itself unless it wins -- so
        however the two re-reads interleave, exactly one cleanup is left queued.
        Without that, the duplicate would run too: a second scan of the list, and
        with dry-run off a second polite reply to whoever is still on it.
        """
        cleanups = await self._pending_cleanups()
        if len(cleanups) <= 1:
            return task

        winner = min(cleanups, key=lambda candidate: (candidate.created, candidate.id))
        for loser in cleanups:
            if loser.id != winner.id:
                await self._cancel_duplicate(loser)

        if winner.id != task.id:
            logger.info(
                "Startup 拒信清扫 task %s lost the race to %s; cancelling the duplicate",
                task.id,
                winner.id,
            )
            return None
        return winner

    async def _cancel_duplicate(self, task: AutomationTask) -> None:
        logger.warning(
            "Cancelling duplicate startup 拒信清扫 task %s (another service queued one first)",
            task.id,
        )
        await self.broker.update_task_status(task.id, status=TaskStatus.CANCELLED)

    async def _pending_cleanups(self) -> list[AutomationTask]:
        """The startup cleanups still waiting to be claimed, oldest first."""
        pending = await self.broker.list_pending_tasks(limit=self.queue_scan_limit)
        return [task for task in pending if self.is_startup_cleanup(task)]


__all__ = [
    "QUEUE_SCAN_LIMIT",
    "SEARCH_TASK_TYPES",
    "STARTUP_CLEANUP_MARKER",
    "StartupCleanupGate",
]
