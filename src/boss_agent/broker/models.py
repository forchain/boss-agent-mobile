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
    worker_id: str | None = None
    locked_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    retry_count: int = 0
    max_retries: int = 2
    logs: list[str] = Field(default_factory=list)
    error_message: str | None = None
    created: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated: datetime = Field(default_factory=lambda: datetime.now(UTC))


POCKETBASE_AUTOMATION_TASKS_SCHEMA = {
    "name": "automation_tasks",
    "type": "base",
    "fields": [
        {
            "name": "id",
            "type": "text",
            "primaryKey": True,
            "required": True,
        },
        {
            "name": "task_type",
            "type": "select",
            "required": True,
            "values": [t.value for t in TaskType],
        },
        {
            "name": "status",
            "type": "select",
            "required": True,
            "values": [s.value for s in TaskStatus],
        },
        {
            "name": "payload",
            "type": "json",
            "required": False,
        },
        {
            "name": "worker_id",
            "type": "text",
            "required": False,
        },
        {
            "name": "locked_at",
            "type": "date",
            "required": False,
        },
        {
            "name": "last_heartbeat_at",
            "type": "date",
            "required": False,
        },
        {
            "name": "logs",
            "type": "json",
            "required": False,
        },
        {
            "name": "error_message",
            "type": "text",
            "required": False,
        },
        {
            "name": "created",
            "type": "date",
            "required": False,
        },
        {
            "name": "updated",
            "type": "date",
            "required": False,
        },
    ],
    "indexes": [
        "CREATE INDEX idx_status_created ON automation_tasks (status, created)",
        "CREATE INDEX idx_worker_id ON automation_tasks (worker_id)",
    ],
}


POCKETBASE_CANDIDATE_PROFILES_SCHEMA = {
    "name": "candidate_profiles",
    "type": "base",
    "fields": [
        {
            "name": "id",
            "type": "text",
            "primaryKey": True,
            "required": True,
        },
        {
            "name": "user_id",
            "type": "text",
            "required": True,
        },
        {
            "name": "name",
            "type": "text",
            "required": False,
        },
        {
            "name": "years_of_experience",
            "type": "number",
            "required": False,
        },
        {
            "name": "education",
            "type": "json",
            "required": False,
        },
        {
            "name": "core_skills",
            "type": "json",
            "required": False,
        },
        {
            "name": "project_highlights",
            "type": "json",
            "required": False,
        },
        {
            "name": "target_positions",
            "type": "json",
            "required": False,
        },
        {
            "name": "raw_summary",
            "type": "text",
            "required": False,
        },
        {
            "name": "created",
            "type": "date",
            "required": False,
        },
        {
            "name": "updated",
            "type": "date",
            "required": False,
        },
    ],
    "indexes": [
        "CREATE UNIQUE INDEX idx_candidate_user_id ON candidate_profiles (user_id)",
    ],
}

