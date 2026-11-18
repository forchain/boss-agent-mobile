"""
src/boss_agent/core/task/domain/models.py
=========================================
Domain model of the Automation Task (issue #449; moved from ``broker/models.py``).

The entity is a plain dataclass so task lifecycle language does not depend on a
validation framework. Construction stays keyword-only, matching the pydantic model's
ergonomics, and keeps the coercions callers rely on (string task types/statuses and ISO
datetimes) so broker rows and test fixtures build it the same way they always did.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class TaskType(StrEnum):
    """Supported task types executable by the Automation Worker."""

    CHECK_LOGIN = "CHECK_LOGIN"
    SCRAPE_JOBS = "SCRAPE_JOBS"
    AUTO_APPLY = "AUTO_APPLY"
    CHECK_CHAT = "CHECK_CHAT"


class TaskStatus(StrEnum):
    """Lifecycle states of an automation task."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED_FOR_TAKEOVER = "paused_for_takeover"
    RESUMING = "resuming"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def is_terminal(self) -> bool:
        """Return True if this status represents a terminal state."""
        return self in (TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.CANCELLED)


def _coerce_datetime(value: Any) -> datetime | None:
    """Parse a broker row's ISO timestamp; datetimes pass through untouched."""
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


@dataclass(kw_only=True)
class AutomationTask:
    """Representation of an automation task in the State Stream Broker."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:15])
    task_type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    payload: dict[str, Any] = field(default_factory=dict)
    #: Task Provenance (GLOSSARY.md): manual | test | scheduler. A real attribute on the
    #: record, so startup reclamation and the dashboard can act on it without reading a
    #: payload marker. Legacy rows read as "manual".
    source: str = "manual"
    worker_id: str | None = None
    locked_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    retry_count: int = 0
    max_retries: int = 2
    logs: list[str] = field(default_factory=list)
    error_message: str | None = None
    created: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not isinstance(self.task_type, TaskType):
            self.task_type = TaskType(self.task_type)
        if not isinstance(self.status, TaskStatus):
            self.status = TaskStatus(self.status)
        self.locked_at = _coerce_datetime(self.locked_at)
        self.last_heartbeat_at = _coerce_datetime(self.last_heartbeat_at)
        self.created = _coerce_datetime(self.created)  # type: ignore[assignment]
        self.updated = _coerce_datetime(self.updated)  # type: ignore[assignment]

    def model_dump(self) -> dict[str, Any]:
        """The persisted key/value view the pydantic model exposed to callers."""
        return asdict(self)


__all__ = ["AutomationTask", "TaskStatus", "TaskType"]
