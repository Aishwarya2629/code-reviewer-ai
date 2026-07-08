"""
/api/v1/review — Code review endpoint.

Two execution modes (controlled by ?async=true query param):
  Sync  (default):  run pipeline inline, return full result immediately (~10-30s)
  Async (?async=1): enqueue Celery job, return {job_id} immediately, poll /jobs/{id}

Cache layer (before pipeline):
  1. Check semantic cache — if hit, return in <100ms
  2. On miss, run pipeline
  3. Store result in cache for future identical/similar submissions

Rate limiting:
  Enforced before cache check — even cache-served requests count toward quota
  (prevents cache-stuffing abuse).

Metrics:
  Increments pipeline_runs_total, security_issues_found_total, records request_metrics row.
"""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.agents.graph import run_review_pipeline
from app.core.config import get_settings
from app.core.exceptions import InvalidInputError, InputTooLargeError
from app.core.logging_config import get_logger
from app.core.metrics import (
    pipeline_runs_total, pipeline_duration_seconds,
    security_issues_found_total,
)
from app.middleware.rate_limiter import check_rate_limit, RateLimitExceeded, get_remaining
from app.models.schemas import (
    ReviewRequest, ReviewResponse, ComplexityInfo,
    OptimizationChange, SecurityIssue,
)
from app.services.cache_service import lookup as cache_lookup, store as cache_store

router = APIRouter()
logger = get_logger(__name__)
settings = get_settings()


def _record_metrics(request: Request, result: dict, duration_ms: int,
                    cache_hit: bool, language: str) -> None:
    """Write one row to request_metrics table (best-effort, non-blocking)."""
    try:
        from app.db.connection import db_available, get_conn
        if not db_available():
            return
        tenant = getattr(request.state, "tenant", {})
        tenant_api_key = tenant.get("api_key") if tenant else None

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO request_metrics
                        (request_id, endpoint, method, status_code,
                         duration_ms, provider_used, cache_hit,
                         security_issues_count, language)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    result.get("request_id", str(uuid.uuid4())),
                    "/api/v1/review", "POST", 200,
                    duration_ms,
                    result.get("provider_used"),
                    cache_hit,
                    len(result.get("security_issues", [])),
                    language,
                ))
    except Exception as exc:
        logger.debug(f"Metrics record failed (non-fatal): {exc}")


@router.post(
    "/review",
    response_model=ReviewResponse,
    summary="Analyse and optimise source code",
    responses={
        202: {"description": "Job enqueued (async mode) — poll /jobs/{job_id}"},
        413: {"description": "Input too large"},
        422: {"description": "Invalid input"},
        429: {"description": "Rate limit exceeded"},
        503: {"description": "All LLM providers unavailable"},
    },
)
async def review_code(
    payload: ReviewRequest,
    request: Request,
    async_mode: bool = Query(False, alias="async", description="Return job_id for async polling"),
):
    request_id = str(request.state.request_id)
    tenant     = getattr(request.state, "tenant", {}) or {}

    # ── Rate limiting ─────────────────────────────────────────────────────────
    try:
        check_rate_limit(
            tenant_api_key=tenant.get("api_key", "anonymous"),
            rpm=tenant.get("rpm", settings.RATE_LIMIT_FREE_RPM),
        )
    except RateLimitExceeded as exc:
        remaining = 0
        return JSONResponse(
            status_code=429,
            content={"error": "RateLimitExceeded", "detail": str(exc), "request_id": request_id},
            headers={
                "X-RateLimit-Limit":     str(exc.limit),
                "X-RateLimit-Remaining": "0",
                "Retry-After":           str(exc.window_s),
            },
        )

    # ── Input validation ──────────────────────────────────────────────────────
    if len(payload.code) > settings.MAX_CODE_LENGTH:
        raise InputTooLargeError("code", settings.MAX_CODE_LENGTH)

    language = payload.language.value

    # ── Async mode: enqueue job and return immediately ────────────────────────
    if async_mode:
        from app.workers.tasks import review_code_task
        task = review_code_task.delay(
            code=payload.code,
            language=language,
            request_id=request_id,
            tenant_id=tenant.get("name", "unknown"),
        )
        return JSONResponse(
            status_code=202,
            content={"job_id": task.id, "request_id": request_id,
                     "poll_url": f"/api/v1/jobs/{task.id}"},
        )

    # ── Semantic cache check ──────────────────────────────────────────────────
    t0 = time.perf_counter()
    cached = cache_lookup(payload.code, language)

    if cached:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        cached["request_id"] = request_id
        pipeline_duration_seconds.labels(cached="true").observe(duration_ms / 1000)
        _record_metrics(request, cached, duration_ms, cache_hit=True, language=language)
        logger.info(f"Cache hit request_id={request_id} duration_ms={duration_ms}")

        remaining = get_remaining(tenant.get("api_key", "anon"),
                                  tenant.get("rpm", settings.RATE_LIMIT_FREE_RPM))
        return _build_response(cached, request_id, remaining)

    # ── Run pipeline ──────────────────────────────────────────────────────────
    state = run_review_pipeline(
        code=payload.code,
        language=language,
        request_id=request_id,
    )
    duration_ms = int((time.perf_counter() - t0) * 1000)

    # Prometheus pipeline metrics
    pipeline_runs_total.labels(
        language=state.get("detected_language", "unknown"),
        already_optimal=str(state.get("already_optimal", False)),
    ).inc()
    pipeline_duration_seconds.labels(cached="false").observe(duration_ms / 1000)

    for finding in state.get("security_findings", []):
        security_issues_found_total.labels(
            severity=finding.get("severity", "INFO"),
            rule_id=finding.get("rule_id", "unknown"),
        ).inc()

    # Build response dict for cache storage
    result_dict = {
        "request_id":      request_id,
        "valid":           state.get("input_type") != "invalid",
        "already_optimal": state.get("already_optimal", False),
        "detected_language": state.get("detected_language", "unknown"),
        "original_code":   payload.code,
        "optimized_code":  state.get("optimized_code", payload.code),
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
        "provider_used":   state.get("provider_used"),
        "pipeline_ms":     duration_ms,
    }

    # Store in cache (best-effort)
    cache_store(payload.code, language, result_dict, tenant.get("api_key"))

    _record_metrics(request, result_dict, duration_ms, cache_hit=False, language=language)

    remaining = get_remaining(tenant.get("api_key", "anon"),
                              tenant.get("rpm", settings.RATE_LIMIT_FREE_RPM))
    return _build_response(result_dict, request_id, remaining)


def _build_response(r: dict, request_id: str, remaining: int) -> ReviewResponse:
    security_issues = [
        SecurityIssue(
            rule_id=f.get("rule_id", "?"),
            severity=f.get("severity", "INFO"),
            line=f.get("line"),
            description=f.get("description", ""),
            recommendation=f.get("recommendation", ""),
        )
        for f in r.get("security_issues", [])
    ]
    changes = [
        OptimizationChange(
            category=c.get("category", "general"),
            description=c.get("description", ""),
            impact=c.get("impact", ""),
        )
        for c in r.get("changes_made", [])
    ]
    bc = r.get("before_complexity", {})
    ac = r.get("after_complexity", {})
    return ReviewResponse(
        request_id=request_id,
        valid=r.get("valid", True),
        already_optimal=r.get("already_optimal", False),
        detected_language=r.get("detected_language", "unknown"),
        original_code=r.get("original_code", ""),
        optimized_code=r.get("optimized_code", ""),
        before_complexity=ComplexityInfo(
            time=bc.get("time", "O(?)"),
            space=bc.get("space", "O(?)"),
            reasoning=bc.get("reasoning", ""),
        ),
        after_complexity=ComplexityInfo(
            time=ac.get("time", "O(?)"),
            space=ac.get("space", "O(?)"),
            reasoning=ac.get("reasoning", ""),
        ),
        security_issues=security_issues,
        changes_made=changes,
        explanation=r.get("explanation", ""),
        analysis=r.get("analysis", ""),
        fallback_used=r.get("fallback_used", False),
        provider_used=r.get("provider_used"),
        pipeline_ms=r.get("pipeline_ms"),
    )
