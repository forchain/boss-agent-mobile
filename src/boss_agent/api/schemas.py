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


class ResumeTaskResponse(BaseModel):
    """Response after requesting task resume from takeover."""

    task_id: str
    status: TaskStatus
    message: str = "Task resumed successfully"


# Candidate Profile Schemas
class CandidateProfileSchema(BaseModel):
    """Structured candidate profile schema."""

    name: str = "求职者"
    years_of_experience: int = 0
    education: list[dict[str, str]] = Field(default_factory=list)
    core_skills: list[str] = Field(default_factory=list)
    project_highlights: list[dict[str, str]] = Field(default_factory=list)
    target_positions: list[str] = Field(default_factory=list)
    raw_summary: str = ""


class CandidateProfileResponse(BaseModel):
    """Response containing structured candidate profile."""

    success: bool = True
    profile: CandidateProfileSchema
    message: str = "Candidate profile loaded successfully"


class UpdateCandidateProfileRequest(BaseModel):
    """Request payload to update candidate profile."""

    name: str | None = None
    years_of_experience: int | None = None
    education: list[dict[str, str]] | None = None
    core_skills: list[str] | None = None
    project_highlights: list[dict[str, str]] | None = None
    target_positions: list[str] | None = None
    raw_summary: str | None = None


# LLM Settings Schemas
class LLMSettingsSchema(BaseModel):
    """LLM provider and model configuration schema."""

    provider: str = "openai"
    model: str = "gpt-4o-mini"
    base_url: str = "https://api.openai.com/v1"
    api_key_masked: str = "******"
    temperature: float = 0.3


class UpdateLLMSettingsRequest(BaseModel):
    """Payload to update LLM configuration."""

    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    temperature: float | None = None


# Match Sandbox Schemas
class MatchEvaluateRequest(BaseModel):
    """Payload for instant live job match evaluation sandbox."""

    job_title: str
    company_name: str = "目标公司"
    job_description: str
    salary_range: str = "面议"


class MatchEvaluateResponse(BaseModel):
    """Response for instant job match evaluation."""

    match_score: int
    jd_key_requirements: list[str] = Field(default_factory=list)
    match_reasons: list[str] = Field(default_factory=list)
    greeting_message: str

