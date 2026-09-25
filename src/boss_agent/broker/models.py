"""
src/boss_agent/broker/models.py
===============================
Domain models, schemas, and enums for State Stream Task Broker (Issue #27).
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


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


class AutomationTask(BaseModel):
    """Representation of an automation task in the State Stream Broker."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:15])
    task_type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    payload: dict[str, Any] = Field(default_factory=dict)
    #: Task Provenance (CONTEXT.md): manual | test | scheduler. A real attribute on the
    #: record, so startup reclamation and the dashboard can act on it without reading a
    #: payload marker. Legacy rows read as "manual".
    source: str = "manual"
    worker_id: str | None = None
    locked_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    retry_count: int = 0
    max_retries: int = 2
    logs: list[str] = Field(default_factory=list)
    error_message: str | None = None
    created: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated: datetime = Field(default_factory=lambda: datetime.now(UTC))
