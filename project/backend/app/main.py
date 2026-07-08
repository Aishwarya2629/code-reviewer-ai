"""
FastAPI application entrypoint — wires all middleware, routes, and startup hooks.

Startup sequence (lifespan):
  1. Init DB connection pool + run schema migrations
  2. Log available LLM providers
  3. Log readiness

Middleware stack (applied bottom-up, executed top-down):
  1. Request-ID injection   — UUID per request, set in ContextVar
  2. TenantMiddleware       — resolve X-API-Key → tenant object
  3. CORS
  4. Request timing         — measure duration, record to Prometheus

Special routes:
  GET /metrics   — Prometheus scrape endpoint (text/plain)
  GET /          — service info JSON
"""
from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.core.exceptions import CodeReviewerError
from app.core.logging_config import configure_logging, get_logger, request_id_var
from app.core.metrics import (
    http_requests_total, http_request_duration_seconds, get_metrics_output
)
from app.middleware.auth import TenantMiddleware
from app.services.llm_service import available_providers

settings = get_settings()
configure_logging(level=settings.LOG_LEVEL, fmt=settings.LOG_FORMAT)
logger = get_logger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # DB init (non-fatal — app runs without it, just no cache/analytics)
    try:
        from app.db.connection import init_db
        init_db()
    except Exception as exc:
        logger.warning(f"DB init failed — cache and analytics disabled: {exc}")

    providers = available_providers()
    if providers:
        logger.info(f"LLM providers ready: {providers}")
    else:
        logger.warning("No LLM providers configured — set at least one API key in .env")

    logger.info(f"{settings.APP_NAME} v{settings.APP_VERSION} ready")
    yield
    logger.info(f"{settings.APP_NAME} shutting down")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Production-grade multi-agent code review pipeline. "
        "LangGraph · Security Scanner · pgvector Semantic Cache · "
        "Celery Queue · Circuit Breaker · Prometheus Metrics · GitHub Webhook"
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
)

# ── Tenant resolution (before rate limiter, after request-ID) ─────────────────
app.add_middleware(TenantMiddleware)


# ── Request-ID + timing middleware ────────────────────────────────────────────

@app.middleware("http")
async def request_lifecycle(request: Request, call_next) -> Response:
    rid   = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    token = request_id_var.set(rid)
    request.state.request_id = rid

    t0 = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - t0

    # Prometheus metrics
    endpoint = request.url.path
    http_requests_total.labels(
        method=request.method,
        endpoint=endpoint,
        status_code=str(response.status_code),
        tenant=getattr(getattr(request.state, "tenant", {}), "get", lambda k, d="": d)("name", "unknown"),
    ).inc()
    http_request_duration_seconds.labels(
        method=request.method,
        endpoint=endpoint,
    ).observe(duration)

    response.headers["X-Request-ID"] = rid
    request_id_var.reset(token)
    return response


# ── Global exception handlers ─────────────────────────────────────────────────

@app.exception_handler(CodeReviewerError)
async def domain_error_handler(request: Request, exc: CodeReviewerError):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "request_id": str(getattr(request.state, "request_id", "-")),
            "error":  exc.__class__.__name__,
            "detail": exc.detail,
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled: {exc!r}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "request_id": str(getattr(request.state, "request_id", "-")),
            "error":  "InternalServerError",
            "detail": "An unexpected error occurred.",
        },
    )


# ── Routes ────────────────────────────────────────────────────────────────────
app.include_router(v1_router)


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics():
    """Prometheus scrape endpoint."""
    body, content_type = get_metrics_output()
    return Response(content=body, media_type=content_type)


@app.get("/", include_in_schema=False)
async def root():
    return {
        "service":  settings.APP_NAME,
        "version":  settings.APP_VERSION,
        "docs":     "/docs",
        "health":   "/api/v1/health",
        "metrics":  "/metrics",
    }
