"""
boss_agent.core.search.use_cases.dispatch
=========================================
The per-tick schedule decision and the task a due SavedSearch dispatches.

The AutomationScheduler still owns the polling loop and the broker writes, but the two
rules it applies to every strategy — does the schedule fire now, and which launch does
that strategy produce — are domain rules. They take an injected instant and injected
acknowledgment settings so neither a wall clock nor a settings file is read here.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from boss_agent.core.search.domain.launch import (
    LaunchSource,
    TaskKind,
    TaskLaunch,
    build_chat_cleanup_launch,
    build_launch,
)
from boss_agent.core.search.domain.models import SavedSearch
from boss_agent.core.search.use_cases.cron import is_cron_match

if TYPE_CHECKING:
    from boss_agent.rejection import ChatAcknowledgmentSettings


def last_run_is_current_minute(raw: str | None, now: datetime) -> bool:
    """Whether a stored ``last_run_at`` falls inside ``now``'s minute.

    A malformed or absent timestamp is not a reason to withhold a dispatch: the guard
    exists to stop the same minute firing twice, and an unreadable stamp cannot prove
    it already did.
    """
    if not raw:
        return False
    try:
        last_run_dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return False
    if last_run_dt.tzinfo is not None and now.tzinfo is not None:
        return int(last_run_dt.timestamp() // 60) == int(now.timestamp() // 60)
    return (
        last_run_dt.year == now.year
        and last_run_dt.month == now.month
        and last_run_dt.day == now.day
        and last_run_dt.hour == now.hour
        and last_run_dt.minute == now.minute
    )


def should_dispatch(search: SavedSearch, now: datetime) -> bool:
    """Whether this strategy's schedule fires at ``now`` and has not fired this minute.

    An invalid cron expression is allowed to raise here; the caller owns the warning
    it logs, which is the only environment-specific part of the decision.
    """
    if not search.is_enabled:
        return False
    if not search.cron_expression or not search.cron_expression.strip():
        return False
    if not is_cron_match(search.cron_expression, now):
        return False
    return not last_run_is_current_minute(search.last_run_at, now)


def build_dispatch(
    search: SavedSearch,
    *,
    source: LaunchSource,
    acknowledgment: ChatAcknowledgmentSettings | None = None,
) -> TaskLaunch:
    """Resolve the worker task a strategy dispatches, and its payload.

    Both shapes come from the shared launch builders, so a scheduled run of a
    SavedSearch is the *same task* a manual launch of it produces. ``mode=None`` lets
    the configured ``chat.dry_run`` win: a scheduled drill is an operator decision, not
    a scheduler convention.
    """
    if search.is_chat_cleanup:
        return build_chat_cleanup_launch(
            source=source,
            search=search,
            acknowledgment=acknowledgment,
        )
    return build_launch(TaskKind.SEARCH, source=source, search=search)
