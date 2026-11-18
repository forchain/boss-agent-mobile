"""
src/boss_agent/adapters/inbound/worker/scheduler.py
===================================================
Lightweight Cron scheduler engine for periodic triggering of SavedSearch strategies.
"""

from __future__ import annotations

import asyncio
import logging
import os
import zoneinfo
from datetime import UTC, datetime, tzinfo

from boss_agent.adapters.outbound.config.settings import (
    resolve_chat_acknowledgment_settings,
    resolve_run_cleanup_on_startup,
)
from boss_agent.core.search.domain.launch import LaunchSource
from boss_agent.core.search.use_cases.dispatch import build_dispatch, should_dispatch
from boss_agent.core.task.domain.models import AutomationTask
from boss_agent.core.task.interfaces.broker import BaseTaskBroker
from boss_agent.core.task.use_cases.startup_cleanup import StartupCleanupGate

logger = logging.getLogger("boss_agent.scheduler")


def get_scheduler_timezone() -> tzinfo:
    """Get the timezone for cron schedule evaluation.

    Defaults to the local system timezone. Can be overridden by
    SCHEDULER_TIMEZONE environment variable (e.g. 'Asia/Shanghai').
    """
    tz_env = os.getenv("SCHEDULER_TIMEZONE")
    if tz_env and tz_env.strip():
        try:
            return zoneinfo.ZoneInfo(tz_env.strip())
        except Exception as e:
            logger.warning("Invalid SCHEDULER_TIMEZONE '%s': %s", tz_env, e)
    local_tz = datetime.now().astimezone().tzinfo
    return local_tz if local_tz is not None else UTC


class AutomationScheduler:
    """Cron scheduling manager for SavedSearch automated task dispatching."""

    def __init__(
        self,
        broker: BaseTaskBroker,
        poll_interval_sec: float = 30.0,
        startup_gate: StartupCleanupGate | None = None,
        tz: tzinfo | None = None,
    ) -> None:
        self.broker = broker
        self.poll_interval_sec = poll_interval_sec
        self.startup_gate = startup_gate or StartupCleanupGate(
            broker,
            enabled=resolve_run_cleanup_on_startup(),
            acknowledgment=resolve_chat_acknowledgment_settings(),
        )
        self.tz = tz or get_scheduler_timezone()
        self._running = False

    async def run_once(self, now: datetime | None = None) -> list[AutomationTask]:
        """Evaluate all enabled saved searches and dispatch tasks for matching schedules.

        The startup 拒信清扫 is queued first (#230). The gate queues it once per
        process and no-ops when the worker daemon already queued one, so the two
        services cannot double-dispatch it.
        """
        await self.startup_gate.arm()

        if now is None:
            now = datetime.now(self.tz)

        saved_searches = await self.broker.saved_searches.list_saved_searches()
        dispatched_tasks: list[AutomationTask] = []

        for search in saved_searches:
            # Evaluate cron match against current minute; the same-minute guard lives in
            # the use case so it is testable without this loop.
            try:
                if not should_dispatch(search, now):
                    continue
            except Exception as e:
                logger.warning(
                    "Failed to evaluate cron '%s' for search %s: %s",
                    search.cron_expression,
                    search.id,
                    e,
                )
                continue

            # Resolve what this strategy dispatches. The edge resolves the triage
            # settings once, and only for the cleanup that reads them.
            launch = build_dispatch(
                search,
                source=LaunchSource.SCHEDULER,
                acknowledgment=(
                    resolve_chat_acknowledgment_settings() if search.is_chat_cleanup else None
                ),
            )

            task = await self.broker.create_task(
                task_type=launch.task_type,
                payload=launch.payload,
                source=launch.source.value,
            )
            dispatched_tasks.append(task)

            # Update last_run_at timestamp on saved search. A failed write does not undo
            # the dispatch that already happened, so it is not fatal — but it does defeat
            # the same-minute guard above, because the next tick re-reads the stale
            # timestamp. Say so rather than let the search re-dispatch silently.
            search.last_run_at = now.isoformat()
            persisted = None
            try:
                persisted = await self.broker.saved_searches.save_saved_search(search)
            except Exception as e:
                logger.warning(
                    "Could not persist last_run_at for search %s (%s): %s; it may dispatch "
                    "again within this minute",
                    search.id,
                    search.name,
                    e,
                )
            if persisted is None:
                logger.warning(
                    "Could not persist last_run_at for search %s (%s); it may dispatch "
                    "again within this minute",
                    search.id,
                    search.name,
                )
            logger.info(
                "Scheduled %s task %s dispatched for search %s (%s)",
                task.task_type.value,
                task.id,
                search.id,
                search.name,
            )

        return dispatched_tasks

    async def run_forever(self) -> None:
        """Background continuous scheduler loop."""
        self._running = True
        logger.info("AutomationScheduler started background polling loop")
        while self._running:
            try:
                await self.run_once()
            except Exception as ex:
                logger.error("Error occurred in scheduler loop: %s", ex)
            await asyncio.sleep(self.poll_interval_sec)

    def stop(self) -> None:
        """Signal the scheduler to stop."""
        self._running = False
