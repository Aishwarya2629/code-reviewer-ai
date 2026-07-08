"""
Prometheus metrics — single registry, imported everywhere.

Metrics exposed at GET /metrics (text/plain Prometheus format).

Why Prometheus?
  Pull-based model: Prometheus scrapes /metrics on its own schedule.
  No SDK calls in hot paths that could add latency.
  Counters/histograms are thread-safe and extremely low overhead.
"""
from __future__ import annotations

from prometheus_client import (
    Counter, Histogram, Gauge, CollectorRegistry, CONTENT_TYPE_LATEST,
    generate_latest, multiprocess, REGISTRY,
)

# ── HTTP metrics ──────────────────────────────────────────────────────────────

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code", "tenant"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

# ── LLM metrics ───────────────────────────────────────────────────────────────

llm_requests_total = Counter(
    "llm_requests_total",
    "Total LLM provider invocations",
    ["provider", "status"],   # status: success | failure | circuit_open
)

llm_latency_seconds = Histogram(
    "llm_latency_seconds",
    "LLM provider response latency",
    ["provider"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

# ── Pipeline metrics ──────────────────────────────────────────────────────────

pipeline_duration_seconds = Histogram(
    "pipeline_duration_seconds",
    "Full review pipeline latency",
    ["cached"],   # cached: true | false
    buckets=[1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0],
)

pipeline_runs_total = Counter(
    "pipeline_runs_total",
    "Total pipeline runs",
    ["language", "already_optimal"],
)

security_issues_found_total = Counter(
    "security_issues_found_total",
    "Total security issues found by scanner",
    ["severity", "rule_id"],
)

# ── Cache metrics ─────────────────────────────────────────────────────────────

cache_hits_total   = Counter("cache_hits_total",   "Semantic cache hits")
cache_misses_total = Counter("cache_misses_total",  "Semantic cache misses")

# ── Circuit breaker metrics ───────────────────────────────────────────────────

circuit_breaker_state = Gauge(
    "circuit_breaker_state",
    "Circuit breaker state per provider (0=CLOSED, 1=HALF_OPEN, 2=OPEN)",
    ["provider"],
)

# ── Queue metrics ─────────────────────────────────────────────────────────────

celery_jobs_enqueued_total = Counter(
    "celery_jobs_enqueued_total",
    "Total Celery jobs enqueued",
    ["task_name"],
)

celery_active_jobs = Gauge(
    "celery_active_jobs",
    "Currently active Celery jobs",
)

# ── Tenant metrics ────────────────────────────────────────────────────────────

tenant_requests_total = Counter(
    "tenant_requests_total",
    "Requests per tenant",
    ["tenant", "tier"],
)

rate_limit_hits_total = Counter(
    "rate_limit_hits_total",
    "Rate limit rejections per tenant",
    ["tenant"],
)

# ── Helpers ───────────────────────────────────────────────────────────────────

_CB_STATE_MAP = {"CLOSED": 0, "HALF_OPEN": 1, "OPEN": 2}


def update_circuit_breaker_metrics() -> None:
    """Called periodically or on each request to keep CB gauge current."""
    try:
        from app.services.circuit_breaker import get_all_states
        for provider, state_str in get_all_states().items():
            circuit_breaker_state.labels(provider=provider).set(
                _CB_STATE_MAP.get(state_str, 0)
            )
    except Exception:
        pass


def get_metrics_output() -> tuple[bytes, str]:
    """Return (body, content_type) for the /metrics endpoint."""
    update_circuit_breaker_metrics()
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
