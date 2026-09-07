"""Unit tests for AutomationScheduler and Cron parsing."""

from datetime import UTC, datetime

import pytest

from boss_agent.broker.models import TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.models import SavedSearch
from boss_agent.scheduler import (
    AutomationScheduler,
    get_next_cron_run,
    is_cron_match,
    parse_cron_field,
)


def test_parse_cron_field():
    # Wildcard
    assert parse_cron_field("*", 0, 5) == {0, 1, 2, 3, 4, 5}
    # Exact
    assert parse_cron_field("15", 0, 59) == {15}
    # List
    assert parse_cron_field("1,3,5", 0, 10) == {1, 3, 5}
    # Range
    assert parse_cron_field("1-4", 0, 10) == {1, 2, 3, 4}
    # Step
    assert parse_cron_field("*/15", 0, 59) == {0, 15, 30, 45}
    # Range with step
    assert parse_cron_field("10-20/5", 0, 30) == {10, 15, 20}


def test_is_cron_match():
    # 2026-09-07 is Monday
    mon_9am = datetime(2026, 9, 7, 9, 0, 0, tzinfo=UTC)
    mon_901am = datetime(2026, 9, 7, 9, 1, 0, tzinfo=UTC)
    mon_10am = datetime(2026, 9, 7, 10, 0, 0, tzinfo=UTC)
    # 2026-09-06 is Sunday
    sun_9am = datetime(2026, 9, 6, 9, 0, 0, tzinfo=UTC)

    # 1. Daily at 09:00
    daily_9am_cron = "0 9 * * *"
    assert is_cron_match(daily_9am_cron, mon_9am) is True
    assert is_cron_match(daily_9am_cron, mon_901am) is False
    assert is_cron_match(daily_9am_cron, mon_10am) is False
    assert is_cron_match(daily_9am_cron, sun_9am) is True

    # 2. Weekdays at 09:00 (1-5 is Mon-Fri)
    weekday_cron = "0 9 * * 1-5"
    assert is_cron_match(weekday_cron, mon_9am) is True
    assert is_cron_match(weekday_cron, sun_9am) is False

    # 3. Every 15 minutes
    step_cron = "*/15 * * * *"
    assert is_cron_match(step_cron, datetime(2026, 9, 7, 14, 0, tzinfo=UTC)) is True
    assert is_cron_match(step_cron, datetime(2026, 9, 7, 14, 15, tzinfo=UTC)) is True
    assert is_cron_match(step_cron, datetime(2026, 9, 7, 14, 30, tzinfo=UTC)) is True
    assert is_cron_match(step_cron, datetime(2026, 9, 7, 14, 45, tzinfo=UTC)) is True
    assert is_cron_match(step_cron, datetime(2026, 9, 7, 14, 10, tzinfo=UTC)) is False


def test_get_next_cron_run():
    # From 09:05 on Monday, next run of "0 10 * * *" should be 10:00 on same day
    base_dt = datetime(2026, 9, 7, 9, 5, 0, tzinfo=UTC)
    next_run = get_next_cron_run("0 10 * * *", after=base_dt)
    assert next_run == datetime(2026, 9, 7, 10, 0, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_scheduler_run_once():
    broker = InMemoryTaskBroker()

    from boss_agent.models import FilterConfig, SearchConfig

    # Search 1: Enabled and matches Monday 09:00
    search_active = SavedSearch(
        id="search_active_1",
        name="Active 9am Strategy",
        search=SearchConfig(keyword="AI Agent"),
        filter=FilterConfig(education="硕士"),
        cron_expression="0 9 * * 1-5",
        is_enabled=True,
        target_task_type="AUTO_APPLY",
    )
    # Search 2: Disabled but matches cron
    search_disabled = SavedSearch(
        id="search_disabled_2",
        name="Disabled Strategy",
        search=SearchConfig(keyword="Python"),
        cron_expression="0 9 * * 1-5",
        is_enabled=False,
    )
    # Search 3: Enabled but different time
    search_other_time = SavedSearch(
        id="search_other_3",
        name="Afternoon Strategy",
        search=SearchConfig(keyword="Golang"),
        cron_expression="0 14 * * 1-5",
        is_enabled=True,
        target_task_type="SCRAPE_JOBS",
    )

    await broker.save_saved_search(search_active)
    await broker.save_saved_search(search_disabled)
    await broker.save_saved_search(search_other_time)

    scheduler = AutomationScheduler(broker=broker)

    # Monday at 09:00
    monday_9am = datetime(2026, 9, 7, 9, 0, 0, tzinfo=UTC)
    tasks = await scheduler.run_once(now=monday_9am)

    assert len(tasks) == 1
    task = tasks[0]
    assert task.task_type == TaskType.AUTO_APPLY
    assert task.payload["keyword"] == "AI Agent"
    assert task.payload["saved_search_id"] == "search_active_1"
    assert task.payload["enable_search"] is True
    assert task.payload["enable_filter"] is True
    assert task.payload["scheduled"] is True
    assert task.payload["filter"]["education"] == "硕士"

    # Verify search's last_run_at was updated
    updated_search = await broker.get_saved_search("search_active_1")
    assert updated_search is not None
    assert updated_search.last_run_at is not None

    # Calling run_once in the exact same minute should NOT dispatch again
    same_minute = datetime(2026, 9, 7, 9, 0, 30, tzinfo=UTC)
    duplicate_tasks = await scheduler.run_once(now=same_minute)
    assert len(duplicate_tasks) == 0

    # Test running at 14:00 matches Search 3
    monday_2pm = datetime(2026, 9, 7, 14, 0, 0, tzinfo=UTC)
    afternoon_tasks = await scheduler.run_once(now=monday_2pm)
    assert len(afternoon_tasks) == 1
    assert afternoon_tasks[0].task_type == TaskType.SCRAPE_JOBS
    assert afternoon_tasks[0].payload["saved_search_id"] == "search_other_3"
