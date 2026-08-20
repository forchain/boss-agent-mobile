"""
src/boss_agent/api/schemas.py
=============================
Pydantic request and response models for Backend Application Service API.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from boss_agent.broker.models import TaskStatus, TaskType


class CreateTaskRequest(BaseModel):
    """Payload to create a new automation task."""

    task_type: TaskType
    payload: dict[str, Any] = Field(default_factory=dict)


class CreateTaskResponse(BaseModel):
    """Immediate response after task submission."""

    task_id: str
    status: TaskStatus
    task_type: TaskType
    message: str = "Task queued for execution"


class TaskDetailResponse(BaseModel):
    """Full detail of an automation task."""

    id: str
    task_type: TaskType
    status: TaskStatus
    payload: dict[str, Any] = Field(default_factory=dict)
    worker_id: str | None = None
    locked_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    logs: list[str] = Field(default_factory=list)
    error_message: str | None = None
    created: datetime
    updated: datetime


class CancelTaskResponse(BaseModel):
    """Response after requesting task cancellation."""

    task_id: str
    status: TaskStatus
    message: str = "Task cancelled successfully"
