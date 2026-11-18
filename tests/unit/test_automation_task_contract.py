"""
tests/unit/test_automation_task_contract.py
===========================================
Contract tests for the Automation Task entity (issue #449).

The entity moves from a pydantic model to a plain dataclass. These tests lock the
behavior callers actually rely on — field set, defaults, mapping construction, Task
Provenance defaulting, datetime coercion, dataclass field contract, and extra-key
policy — so the move is provably behavior-preserving.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import UTC, datetime

import pytest

from boss_agent.core.task.domain.models import AutomationTask, TaskStatus, TaskType

#: The persisted shape of an automation task. Dashboard types and the PocketBase
#: collection schema mirror these keys, so a field must not appear or vanish silently.
EXPECTED_FIELDS = (
    "id",
    "task_type",
    "status",
    "payload",
    "source",
    "worker_id",
    "locked_at",
    "last_heartbeat_at",
    "retry_count",
    "max_retries",
    "logs",
    "error_message",
    "created",
    "updated",
)


def test_defaults_are_stable() -> None:
    task = AutomationTask(task_type=TaskType.CHECK_LOGIN)

    assert task.status is TaskStatus.PENDING
    assert task.payload == {}
    # Task Provenance (GLOSSARY.md): a record that carries no source is manual.
    assert task.source == "manual"
    assert task.worker_id is None
    assert task.locked_at is None
    assert task.last_heartbeat_at is None
    assert task.retry_count == 0
    assert task.max_retries == 2
    assert task.logs == []
    assert task.error_message is None
    assert isinstance(task.created, datetime) and task.created.tzinfo is not None
    assert isinstance(task.updated, datetime) and task.updated.tzinfo is not None


def test_default_id_is_a_unique_fifteen_character_slug() -> None:
    first = AutomationTask(task_type=TaskType.CHECK_LOGIN)
    second = AutomationTask(task_type=TaskType.CHECK_LOGIN)

    assert re.fullmatch(r"[0-9a-f]{15}", first.id)
    assert first.id != second.id


def test_mapping_construction_coerces_type_and_status() -> None:
    """Dict-built tasks (broker rows, test fixtures) keep producing enum fields."""
    task = AutomationTask(
        **{
            "id": "abc123",
            "task_type": "AUTO_APPLY",
            "status": "running",
            "payload": {"max_applications": 10},
        }
    )

    assert task.id == "abc123"
    assert task.task_type is TaskType.AUTO_APPLY
    assert task.status is TaskStatus.RUNNING
    assert task.payload == {"max_applications": 10}


def test_absent_provenance_reads_as_manual() -> None:
    task = AutomationTask(task_type=TaskType.CHECK_CHAT)

    assert task.source == "manual"


def test_iso_datetimes_are_coerced() -> None:
    task = AutomationTask(
        task_type=TaskType.CHECK_CHAT,
        locked_at="2026-01-02T03:04:05Z",
        last_heartbeat_at="2026-01-02T03:04:05+00:00",
        created="2026-01-02T03:04:05Z",
        updated="2026-01-02T03:04:05+00:00",
    )

    expected = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert task.locked_at == expected
    assert task.last_heartbeat_at == expected
    assert task.created == expected
    assert task.updated == expected


def test_dataclass_fields_match_the_persisted_key_set() -> None:
    task = AutomationTask(task_type=TaskType.SCRAPE_JOBS)

    assert tuple(f.name for f in dataclasses.fields(AutomationTask)) == EXPECTED_FIELDS
    assert dataclasses.asdict(task)["task_type"] is TaskType.SCRAPE_JOBS


def test_unknown_keyword_arguments_are_rejected() -> None:
    """Unlike pydantic (extra='ignore'), the plain dataclass refuses unknown keys.

    PocketBase adapters map declared columns explicitly into ``AutomationTask`` so a
    future server column never breaks row deserialization (ADR 0021).
    """
    with pytest.raises(TypeError):
        AutomationTask(task_type=TaskType.CHECK_LOGIN, unknown_column="extra")


def test_terminal_status_language_is_stable() -> None:
    assert TaskStatus.SUCCESS.is_terminal() is True
    assert TaskStatus.FAILED.is_terminal() is True
    assert TaskStatus.CANCELLED.is_terminal() is True
    for non_terminal in (
        TaskStatus.PENDING,
        TaskStatus.RUNNING,
        TaskStatus.PAUSED_FOR_TAKEOVER,
        TaskStatus.RESUMING,
    ):
        assert non_terminal.is_terminal() is False
