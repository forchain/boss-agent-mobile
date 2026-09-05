"""
src/boss_agent/scheduler.py
===========================
Lightweight Cron scheduler engine for periodic triggering of SavedSearch strategies.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from boss_agent.broker.models import AutomationTask, TaskType
from boss_agent.broker.pocketbase_adapter import BaseTaskBroker

logger = logging.getLogger("boss_agent.scheduler")


def parse_cron_field(field_str: str, min_val: int, max_val: int) -> set[int]:
    """Parse a single cron field expression into a set of matching integers."""
    result: set[int] = set()
    for part in field_str.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            subparts = part.split("/", 1)
            range_part = subparts[0]
            try:
                step = int(subparts[1])
            except ValueError:
                step = 1
        else:
            range_part = part

        if range_part == "*":
            start, end = min_val, max_val
        elif "-" in range_part:
            start_str, end_str = range_part.split("-", 1)
            try:
                start, end = int(start_str), int(end_str)
            except ValueError:
                continue
        else:
            try:
                start = end = int(range_part)
            except ValueError:
                continue

        if step <= 0:
            step = 1

        for val in range(start, end + 1, step):
            if min_val <= val <= max_val:
                result.add(val)
    return result


def is_cron_match(cron_expr: str, dt: datetime) -> bool:
    """Check if the given datetime matches a standard 5-field cron expression.

    Format: minute hour day_of_month month day_of_week
    """
    fields = cron_expr.strip().split()
    if len(fields) != 5:
        return False

    min_f, hour_f, dom_f, mon_f, dow_f = fields

    # Minute (0-59)
    if dt.minute not in parse_cron_field(min_f, 0, 59):
        return False

    # Hour (0-23)
    if dt.hour not in parse_cron_field(hour_f, 0, 23):
        return False

    # Month (1-12)
    if dt.month not in parse_cron_field(mon_f, 1, 12):
        return False

    # Day of week & Day of month handling
    python_weekday = dt.weekday()  # 0=Mon, 6=Sun
    cron_weekday = 0 if python_weekday == 6 else (python_weekday + 1)
    valid_dows = parse_cron_field(dow_f, 0, 7)
    if 7 in valid_dows:
        valid_dows.add(0)

    valid_doms = parse_cron_field(dom_f, 1, 31)

    dom_restricted = dom_f != "*"
    dow_restricted = dow_f != "*"

    if dom_restricted and dow_restricted:
        # If both DOM and DOW are specified, match if EITHER matches
        return dt.day in valid_doms or cron_weekday in valid_dows
    elif dom_restricted:
        return dt.day in valid_doms
    elif dow_restricted:
        return cron_weekday in valid_dows

    return True


def get_next_cron_run(
    cron_expr: str,
    after: datetime | None = None,
    max_days: int = 30,
) -> datetime | None:
    """Compute the next matching datetime for a cron expression."""
    if after is None:
        after = datetime.now(UTC)

    # Start search at the beginning of the next minute
    cur = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = cur + timedelta(days=max_days)

    while cur <= end:
        if is_cron_match(cron_expr, cur):
            return cur
        cur += timedelta(minutes=1)

    return None


class AutomationScheduler:
    """Cron scheduling manager for SavedSearch automated task dispatching."""

    def __init__(
        self,
        broker: BaseTaskBroker,
        poll_interval_sec: float = 30.0,
    ) -> None:
        self.broker = broker
        self.poll_interval_sec = poll_interval_sec
        self._running = False

    async def run_once(self, now: datetime | None = None) -> list[AutomationTask]:
        """Evaluate all enabled saved searches and dispatch tasks for matching schedules."""
        if now is None:
            now = datetime.now(UTC)

        saved_searches = await self.broker.list_saved_searches()
        dispatched_tasks: list[AutomationTask] = []

        for search in saved_searches:
            if not search.is_enabled:
                continue
            if not search.cron_expression or not search.cron_expression.strip():
                continue

            # Evaluate cron match against current minute
            try:
                if not is_cron_match(search.cron_expression, now):
                    continue
            except Exception as e:
                logger.warning(
                    "Failed to evaluate cron '%s' for search %s: %s",
                    search.cron_expression,
                    search.id,
                    e,
                )
                continue

            # Prevent multiple triggers within the same minute
            if search.last_run_at:
                try:
                    last_run_dt = datetime.fromisoformat(search.last_run_at.replace("Z", "+00:00"))
                    if (
                        last_run_dt.year == now.year
                        and last_run_dt.month == now.month
                        and last_run_dt.day == now.day
                        and last_run_dt.hour == now.hour
                        and last_run_dt.minute == now.minute
                    ):
                        continue
                except Exception:
                    pass

            # Resolve target task type
            task_type_str = search.target_task_type or "AUTO_APPLY"
            try:
                task_type = TaskType(task_type_str)
            except ValueError:
                task_type = TaskType.AUTO_APPLY

            search_dict = search.to_dict()
            payload: dict[str, Any] = {
                "saved_search_id": search.id,
                "keyword": search_dict.get("keyword") or "",
                "enable_search": search.enable_search,
                "enable_filter": search.enable_filter,
                "filter": search_dict.get("filter") or {},
                "min_score": 70,
                "preview_only": False,
                "auto_send": False,
                "preview_timeout_sec": 3.0,
                "scheduled": True,
            }

            task = await self.broker.create_task(
                task_type=task_type,
                payload=payload,
            )
            dispatched_tasks.append(task)

            # Update last_run_at timestamp on saved search
            search.last_run_at = now.isoformat()
            await self.broker.save_saved_search(search)
            logger.info(
                "Scheduled task %s dispatched for search %s (%s)",
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
