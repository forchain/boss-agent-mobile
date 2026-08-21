import uuid
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from boss_agent.api.schemas import (
    CancelTaskResponse,
    CandidateProfileResponse,
    CandidateProfileSchema,
    CreateTaskRequest,
    CreateTaskResponse,
    LLMSettingsSchema,
    MatchEvaluateRequest,
    MatchEvaluateResponse,
    ResumeTaskResponse,
    TaskDetailResponse,
    UpdateCandidateProfileRequest,
    UpdateLLMSettingsRequest,
)
from boss_agent.broker.models import TaskStatus
from boss_agent.broker.pocketbase_adapter import BaseTaskBroker
from boss_agent.matching import JobMatchGreetingService
from boss_agent.memory import ResumeMemoryManager, StructuredCandidateProfile
from boss_agent.models import JobPosting

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


# Candidate Profile Routes
candidate_router = APIRouter(prefix="/api/candidate", tags=["candidate"])


@candidate_router.post("/resume", response_model=CandidateProfileResponse)
async def upload_resume(
    broker: TaskBrokerDep,
    file: Annotated[UploadFile, File()],
) -> CandidateProfileResponse:
    """Upload resume file, parse text, extract structured profile via LLM, and persist."""
    suffix = Path(file.filename or "resume.pdf").suffix
    resumes_dir = Path("resumes")
    resumes_dir.mkdir(parents=True, exist_ok=True)
    temp_target = resumes_dir / f"upload_{uuid.uuid4().hex[:8]}{suffix}"

    contents = await file.read()
    temp_target.write_bytes(contents)

    mgr = ResumeMemoryManager()
    profile = mgr.generate_and_save_memory(temp_target)
    profile_dict = profile.to_dict()
    await broker.save_candidate_profile(profile_dict, user_id="default")

    return CandidateProfileResponse(
        success=True,
        profile=CandidateProfileSchema(**profile_dict),
        message=f"Resume '{file.filename}' processed successfully",
    )


@candidate_router.get("/profile", response_model=CandidateProfileResponse)
async def get_candidate_profile(
    broker: TaskBrokerDep,
) -> CandidateProfileResponse:
    """Fetch active candidate memory profile."""
    data = await broker.get_candidate_profile(user_id="default")
    if not data:
        data = StructuredCandidateProfile().to_dict()
    return CandidateProfileResponse(
        success=True,
        profile=CandidateProfileSchema(**data),
        message="Candidate profile retrieved",
    )


@candidate_router.put("/profile", response_model=CandidateProfileResponse)
async def update_candidate_profile(
    req: UpdateCandidateProfileRequest,
    broker: TaskBrokerDep,
) -> CandidateProfileResponse:
    """Update active candidate memory profile."""
    existing = await broker.get_candidate_profile(user_id="default") or StructuredCandidateProfile().to_dict()
    update_data = req.model_dump(exclude_unset=True)
    merged = {**existing, **update_data}
    saved = await broker.save_candidate_profile(merged, user_id="default")
    return CandidateProfileResponse(
        success=True,
        profile=CandidateProfileSchema(**saved),
        message="Candidate profile updated",
    )


# Settings Routes
settings_router = APIRouter(prefix="/api/settings", tags=["settings"])


def _load_llm_config() -> dict[str, Any]:
    for path in [Path("config/llm.local.yaml"), Path("config/llm.yaml")]:
        if path.exists():
            try:
                return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception:
                pass
    return {}


def _save_llm_config(cfg: dict[str, Any]) -> None:
    Path("config").mkdir(parents=True, exist_ok=True)
    Path("config/llm.local.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True),
        encoding="utf-8",
    )


@settings_router.get("/llm", response_model=LLMSettingsSchema)
async def get_llm_settings() -> LLMSettingsSchema:
    """Get active LLM settings with masked API key."""
    cfg = _load_llm_config()
    llm = cfg.get("llm", {})
    api_key = str(llm.get("api_key", ""))
    masked_key = f"{api_key[:4]}****{api_key[-4:]}" if len(api_key) > 8 else "******"
    return LLMSettingsSchema(
        provider=llm.get("provider", "openai"),
        model=llm.get("model", "gpt-4o-mini"),
        base_url=llm.get("base_url", "https://api.openai.com/v1"),
        api_key_masked=masked_key,
        temperature=float(llm.get("temperature", 0.3)),
    )


@settings_router.put("/llm", response_model=LLMSettingsSchema)
async def update_llm_settings(req: UpdateLLMSettingsRequest) -> LLMSettingsSchema:
    """Update LLM provider and credentials."""
    cfg = _load_llm_config()
    llm = cfg.get("llm", {})
    if req.provider is not None:
        llm["provider"] = req.provider
    if req.model is not None:
        llm["model"] = req.model
    if req.base_url is not None:
        llm["base_url"] = req.base_url
    if req.api_key is not None:
        llm["api_key"] = req.api_key
    if req.temperature is not None:
        llm["temperature"] = req.temperature
    cfg["llm"] = llm
    _save_llm_config(cfg)
    return await get_llm_settings()


# Match Evaluation Routes
match_router = APIRouter(prefix="/api/match", tags=["match"])


@match_router.post("/evaluate", response_model=MatchEvaluateResponse)
async def evaluate_job_match(
    req: MatchEvaluateRequest,
    broker: TaskBrokerDep,
) -> MatchEvaluateResponse:
    """Instant live job match sandbox evaluation."""
    profile_data = await broker.get_candidate_profile(user_id="default")
    profile = (
        StructuredCandidateProfile.from_dict(profile_data)
        if profile_data
        else StructuredCandidateProfile()
    )
    posting = JobPosting(
        title=req.job_title,
        company_name=req.company_name,
        salary_range=req.salary_range,
        job_description=req.job_description,
    )
    matching_svc = JobMatchGreetingService(candidate_profile=profile)
    result = matching_svc.evaluate_and_draft_greeting(job=posting, profile=profile)
    return MatchEvaluateResponse(
        match_score=result.match_score,
        jd_key_requirements=result.jd_key_requirements,
        match_reasons=result.match_reasons,
        greeting_message=result.greeting_message,
    )

