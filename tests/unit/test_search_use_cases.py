"""Core search use cases: cron evaluation, the dispatch decision, and injected config.

The AutomationScheduler keeps its polling loop until the composition-root ticket, but
the two rules it applies per tick are domain rules: whether a strategy's schedule fires
now, and which launch that strategy produces. They live here so they are testable
without a loop, an environment, or a wall clock.
"""

from datetime import UTC, datetime, timedelta

import pytest

from boss_agent.core.chat.domain.rejection import ChatAcknowledgmentSettings
from boss_agent.core.search.domain.launch import LaunchSource, build_chat_cleanup_launch
from boss_agent.core.search.domain.models import (
    FilterConfig,
    SavedSearch,
    SearchConfig,
)
from boss_agent.core.search.use_cases.cron import (
    get_next_cron_run,
    is_cron_match,
    parse_cron_field,
)
from boss_agent.core.search.use_cases.dispatch import build_dispatch, should_dispatch
from boss_agent.core.task.domain.models import TaskType

MONDAY_9AM = datetime(2026, 9, 7, 9, 0, 0, tzinfo=UTC)


def _search(**overrides) -> SavedSearch:
    fields = {
        "id": "s_due",
        "name": "自动沟通",
        "search": SearchConfig(keyword="agent"),
        "filter": FilterConfig(),
        "target_action": "auto_apply",
        "cron_expression": "0 9 * * *",
        "is_enabled": True,
    }
    fields.update(overrides)
    return SavedSearch(**fields)


# --- cron evaluation -------------------------------------------------------


def test_cron_helpers_are_pure_and_live_in_the_core():
    assert parse_cron_field("*/15", 0, 59) == {0, 15, 30, 45}
    assert is_cron_match("0 9 * * *", MONDAY_9AM) is True
    assert is_cron_match("0 9 * * *", MONDAY_9AM + timedelta(minutes=1)) is False
    assert get_next_cron_run("0 9 * * *", after=MONDAY_9AM) == MONDAY_9AM + timedelta(days=1)


# --- dispatch decision -----------------------------------------------------


def test_a_disabled_search_is_never_due():
    assert should_dispatch(_search(is_enabled=False), MONDAY_9AM) is False


def test_a_search_without_a_schedule_is_never_due():
    assert should_dispatch(_search(cron_expression=""), MONDAY_9AM) is False
    assert should_dispatch(_search(cron_expression="   "), MONDAY_9AM) is False


def test_a_search_is_due_exactly_at_its_cron_minute():
    assert should_dispatch(_search(), MONDAY_9AM) is True
    assert should_dispatch(_search(), MONDAY_9AM + timedelta(minutes=1)) is False


def test_a_search_that_already_ran_this_minute_is_not_due_again():
    same_minute = (MONDAY_9AM + timedelta(seconds=10)).isoformat()
    previous_minute = (MONDAY_9AM - timedelta(minutes=1)).isoformat()

    assert should_dispatch(_search(last_run_at=same_minute), MONDAY_9AM) is False
    assert should_dispatch(_search(last_run_at=previous_minute), MONDAY_9AM) is True


def test_a_legacy_pocketbase_timestamp_is_read_as_an_absolute_instant():
    # PocketBase serializes ``+00:00`` as ``Z``; the same-minute guard must see it.
    assert should_dispatch(_search(last_run_at="2026-09-07 09:00:10.000Z"), MONDAY_9AM) is False


def test_the_dispatch_decision_uses_the_injected_now_not_the_wall_clock():
    """A strategy whose cron does not match the injected minute never fires."""
    assert should_dispatch(_search(cron_expression="0 3 * * *"), MONDAY_9AM) is False


# --- which launch a dispatch produces --------------------------------------


def test_a_search_dispatch_is_the_search_launch_with_scheduler_provenance():
    launch = build_dispatch(_search(), source=LaunchSource.SCHEDULER)

    assert launch.task_type is TaskType.AUTO_APPLY
    assert launch.source is LaunchSource.SCHEDULER
    assert launch.payload["target_action"] == "auto_apply"


def test_a_chat_cleanup_dispatch_uses_the_injected_acknowledgment_settings(monkeypatch):
    """Resolved settings arrive as an argument; the core never reads the environment."""
    monkeypatch.setenv("CHAT_DRY_RUN", "true")
    monkeypatch.setenv("CHAT_MAX_SCAN_DEPTH", "99")
    monkeypatch.setenv("CHAT_REJECTION_REPLY_TEXT", "from env")

    cleanup = _search(
        target_task_type="CHECK_CHAT",
        target_action="check_chat",
        search=SearchConfig(keyword=None),
    )
    launch = build_dispatch(
        cleanup,
        source=LaunchSource.SCHEDULER,
        acknowledgment=ChatAcknowledgmentSettings(
            rejection_reply_text="谢谢", max_scan_depth=7, dry_run=False
        ),
    )

    assert launch.task_type is TaskType.CHECK_CHAT
    assert launch.payload["dry_run"] is False
    assert launch.payload["max_scan_depth"] == 7
    assert launch.payload["rejection_reply_text"] == "谢谢"


def test_the_launch_builder_defaults_to_the_domain_acknowledgment_defaults(monkeypatch):
    """Without an injected value the builder uses the declared defaults, not env config."""
    monkeypatch.setenv("CHAT_DRY_RUN", "true")

    launch = build_chat_cleanup_launch(source=LaunchSource.SCHEDULER)

    assert launch.payload["dry_run"] is False
    assert launch.payload["max_scan_depth"] == ChatAcknowledgmentSettings().max_scan_depth


@pytest.mark.asyncio
async def test_the_scheduler_passes_its_resolved_settings_into_the_dispatch():
    """The edge resolves once per tick and the use case never reaches for them."""
    from unittest.mock import patch

    from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
    from boss_agent.scheduler import AutomationScheduler

    broker = InMemoryTaskBroker()
    await broker.saved_searches.save_saved_search(
        _search(
            id="inbox_cleanup",
            target_task_type="CHECK_CHAT",
            target_action="check_chat",
            cron_expression="0 9 * * *",
        )
    )

    scheduler = AutomationScheduler(broker=broker)
    with patch(
        "boss_agent.scheduler.resolve_chat_acknowledgment_settings",
        return_value=ChatAcknowledgmentSettings(dry_run=True, max_scan_depth=7),
    ):
        tasks = await scheduler.run_once(now=MONDAY_9AM)

    assert len(tasks) == 1
    assert tasks[0].payload["dry_run"] is True
    assert tasks[0].payload["max_scan_depth"] == 7
