"""
Celery task definitions.

Each task:
  1. Runs the full pipeline (same code as the sync route)
  2. Stores result in Redis with 1-hour TTL keyed by job_id
  3. Updates Prometheus gauges

The GET /api/v1/jobs/{job_id} route reads the Celery result backend
to return status (PENDING / SUCCESS / FAILURE) and the payload.

Why Celery over asyncio background tasks?
  FastAPI's BackgroundTasks run in the same process — a crashed worker
  loses all queued work. Celery persists tasks in Redis: tasks survive
  restarts, can be retried, and are distributed across multiple workers.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict

from celery import Task
from celery.exceptions import SoftTimeLimitExceeded

from app.workers.celery_app import celery_app
from app.core.logging_config import get_logger
from app.core.metrics import celery_active_jobs, celery_jobs_enqueued_total

logger = get_logger(__name__)


def _pipeline_result_to_dict(state: Dict, original_code: str) -> Dict[str, Any]:
    """Serialise AgentState to a JSON-safe dict matching ReviewResponse shape."""
    return {
        "valid":            state.get("input_type") != "invalid",
        "already_optimal":  state.get("already_optimal", False),
        "detected_language": state.get("detected_language", "unknown"),
        "original_code":    original_code,
        "optimized_code":   state.get("optimized_code", original_code),
        "before_complexity": {
            "time":      state.get("before_time", "O(?)"),
            "space":     state.get("before_space", "O(?)"),
            "reasoning": state.get("complexity_reasoning", ""),
        },
        "after_complexity": {
            "time":  state.get("after_time", "O(?)"),
            "space": state.get("after_space", "O(?)"),
            "reasoning": "",
        },
        "security_issues": state.get("security_findings", []),
        "changes_made":    state.get("changes_made", []),
        "analysis":        state.get("analysis", ""),
        "explanation":     state.get("explanation", ""),
        "fallback_used":   state.get("fallback_used", False),
        "provider_used":   state.get("provider_used", ""),
        "pipeline_ms":     state.get("pipeline_ms"),
    }


@celery_app.task(
    bind=True,
    name="app.workers.tasks.review_code_task",
    max_retries=2,
    default_retry_delay=5,
)
def review_code_task(
    self: Task,
    code: str,
    language: str,
    request_id: str,
    tenant_id: str = "unknown",
) -> Dict[str, Any]:
    """
    Async code review job.
    Called via: review_code_task.delay(code, language, request_id)
    Polled via: GET /api/v1/jobs/{task_id}
    """
    celery_active_jobs.inc()
    try:
        from app.agents.graph import run_review_pipeline
        logger.info(f"[task] review_code request_id={request_id} tenant={tenant_id}")

        state = run_review_pipeline(
            code=code,
            language=language,
            request_id=request_id,
        )
        result = _pipeline_result_to_dict(state, code)
        result["request_id"] = request_id
        return result

    except SoftTimeLimitExceeded:
        logger.warning(f"[task] review_code soft time limit exceeded request_id={request_id}")
        raise self.retry(countdown=10)

    except Exception as exc:
        logger.error(f"[task] review_code failed request_id={request_id} error={exc!r}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=5)
        return {"valid": False, "explanation": f"Task failed: {exc}", "request_id": request_id}

    finally:
        celery_active_jobs.dec()


@celery_app.task(
    bind=True,
    name="app.workers.tasks.solve_problem_task",
    max_retries=2,
    default_retry_delay=5,
)
def solve_problem_task(
    self: Task,
    problem: str,
    language: str,
    request_id: str,
    tenant_id: str = "unknown",
) -> Dict[str, Any]:
    """Async problem solving job."""
    celery_active_jobs.inc()
    try:
        from app.services.llm_service import safe_invoke, parse_json_response
        from app.prompts.templates import PROBLEM_PROMPT

        logger.info(f"[task] solve_problem request_id={request_id}")
        prompt = PROBLEM_PROMPT.format(language=language, problem=problem)
        result = safe_invoke(prompt)
        parsed = parse_json_response(result.content)
        return {
            "valid":        True,
            "solutions":    parsed.get("solutions", []),
            "request_id":   request_id,
            "provider_used": result.provider_used,
            "fallback_used": result.fallback_used,
        }

    except SoftTimeLimitExceeded:
        raise self.retry(countdown=10)

    except Exception as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=5)
        return {"valid": False, "explanation": str(exc), "request_id": request_id}

    finally:
        celery_active_jobs.dec()
