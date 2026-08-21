"""
src/boss_agent/api/routes.py
============================
FastAPI task management routes for submission, querying, and cancellation.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from boss_agent.api.schemas import (
    CancelTaskResponse,
    CreateTaskRequest,
    CreateTaskResponse,
    ResumeTaskResponse,
    TaskDetailResponse,
)
from boss_agent.broker.models import TaskStatus
from boss_agent.broker.pocketbase_adapter import BaseTaskBroker

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def get_task_broker() -> BaseTaskBroker:
    """Dependency provider placeholder for BaseTaskBroker."""
    raise NotImplementedError("Broker dependency must be overridden by app state")


TaskBrokerDep = Annotated[BaseTaskBroker, Depends(get_task_broker)]


@router.post("", response_model=CreateTaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_task(
    req: CreateTaskRequest,
    broker: TaskBrokerDep,
) -> CreateTaskResponse:
    """Submit a new task for background execution."""
    task = await broker.create_task(task_type=req.task_type, payload=req.payload)
    return CreateTaskResponse(
        task_id=task.id,
        status=task.status,
        task_type=task.task_type,
        message="Task queued for execution",
    )


@router.get("/{task_id}", response_model=TaskDetailResponse)
async def get_task_status(
    task_id: str,
    broker: TaskBrokerDep,
) -> TaskDetailResponse:
    """Fetch task status and execution logs."""
    task = await broker.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    return TaskDetailResponse(
        id=task.id,
        task_type=task.task_type,
        status=task.status,
        payload=task.payload,
        worker_id=task.worker_id,
        locked_at=task.locked_at,
        last_heartbeat_at=task.last_heartbeat_at,
        logs=task.logs,
        error_message=task.error_message,
        created=task.created,
        updated=task.updated,
    )


@router.post("/{task_id}/cancel", response_model=CancelTaskResponse)
async def cancel_task(
    task_id: str,
    broker: TaskBrokerDep,
) -> CancelTaskResponse:
    """Cancel a pending or running task."""
    task = await broker.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    if task.status in (TaskStatus.SUCCESS, TaskStatus.FAILED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Task {task_id} is already completed with status {task.status}",
        )

    updated = await broker.update_task_status(
        task_id=task_id,
        status=TaskStatus.CANCELLED,
    )
    return CancelTaskResponse(
        task_id=updated.id,
        status=updated.status,
        message="Task cancelled successfully",
    )


@router.post("/{task_id}/resume", response_model=ResumeTaskResponse)
async def resume_task(
    task_id: str,
    broker: TaskBrokerDep,
) -> ResumeTaskResponse:
    """Resume a task that was paused for manual takeover."""
    task = await broker.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    if task.status != TaskStatus.PAUSED_FOR_TAKEOVER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Task {task_id} is not paused for takeover (current status: {task.status})",
        )

    updated = await broker.update_task_status(
        task_id=task_id,
        status=TaskStatus.RESUMING,
    )
    return ResumeTaskResponse(
        task_id=updated.id,
        status=updated.status,
        message="Task resumed successfully",
    )
