"""
/api/v1/jobs/{job_id} — Poll the result of an async Celery task.

Why polling instead of WebSockets?
  For 10–30s LLM jobs, polling every 2s is simple and reliable.
  WebSockets add complexity (connection management, reconnection logic)
  for minimal benefit at this latency range.

Response contract:
  status = PENDING  → job is queued or running
  status = SUCCESS  → result payload included
  status = FAILURE  → error message included
  status = RETRY    → task is being retried (transient failure)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Any, Dict, Optional

from app.models.schemas import ProblemRequest, ReviewRequest

router = APIRouter()


class JobStatusResponse(BaseModel):
    job_id: str
    status: str                     # PENDING | SUCCESS | FAILURE | RETRY
    result: Optional[Dict[str, Any]] = None
    error:  Optional[str] = None


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    summary="Poll the status of an async review/problem job",
)
async def get_job_status(job_id: str, request: Request):
    try:
        from app.workers.celery_app import celery_app
        async_result = celery_app.AsyncResult(job_id)
        state = async_result.state            # PENDING / STARTED / SUCCESS / FAILURE / RETRY
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Queue unavailable: {exc}")

    if state == "SUCCESS":
        return JobStatusResponse(
            job_id=job_id,
            status="SUCCESS",
            result=async_result.result,
        )

    if state == "FAILURE":
        return JobStatusResponse(
            job_id=job_id,
            status="FAILURE",
            error=str(async_result.result),
        )

    # PENDING / STARTED / RETRY / unknown
    return JobStatusResponse(job_id=job_id, status=state)


class JobSubmitResponse(BaseModel):
    job_id: str
    request_id: str
    poll_url: str
    status: str = "queued"


@router.post(
    "/jobs/review",
    response_model=JobSubmitResponse,
    status_code=202,
    summary="Enqueue an async code review job",
)
async def submit_review_job(payload: ReviewRequest, request: Request):
    """
    Submit a review job to the Celery queue.
    Returns immediately with job_id. Poll GET /api/v1/jobs/{job_id} for result.
    Identical to POST /review?async=true — provided as a dedicated endpoint
    for clients that prefer explicit async semantics.
    """
    from app.workers.tasks import review_code_task
    from app.core.config import get_settings
    from app.core.exceptions import InputTooLargeError

    settings = get_settings()
    request_id = str(request.state.request_id)

    if len(payload.code) > settings.MAX_CODE_LENGTH:
        raise InputTooLargeError("code", settings.MAX_CODE_LENGTH)

    tenant = getattr(request.state, "tenant", {}) or {}
    try:
        task = review_code_task.delay(
            code=payload.code,
            language=payload.language.value,
            request_id=request_id,
            tenant_id=tenant.get("name", "unknown"),
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Queue unavailable: {exc}")

    return JobSubmitResponse(
        job_id=task.id,
        request_id=request_id,
        poll_url=f"/api/v1/jobs/{task.id}",
    )


@router.post(
    "/jobs/problem",
    response_model=JobSubmitResponse,
    status_code=202,
    summary="Enqueue an async problem-solving job",
)
async def submit_problem_job(payload: ProblemRequest, request: Request):
    from app.workers.tasks import solve_problem_task
    from app.core.config import get_settings
    from app.core.exceptions import InputTooLargeError

    settings = get_settings()
    request_id = str(request.state.request_id)

    if len(payload.problem) > settings.MAX_PROBLEM_LENGTH:
        raise InputTooLargeError("problem", settings.MAX_PROBLEM_LENGTH)

    tenant = getattr(request.state, "tenant", {}) or {}
    try:
        task = solve_problem_task.delay(
            problem=payload.problem,
            language=payload.language.value,
            request_id=request_id,
            tenant_id=tenant.get("name", "unknown"),
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Queue unavailable: {exc}")

    return JobSubmitResponse(
        job_id=task.id,
        request_id=request_id,
        poll_url=f"/api/v1/jobs/{task.id}",
    )
