"""
/api/v1/analytics/* — Time-series and aggregated metrics from request_metrics table.

All endpoints return simple JSON that the Streamlit analytics dashboard reads.
Designed to be fast: all queries use indexed columns (tenant_id, created_at).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query

from app.core.logging_config import get_logger
from app.services.circuit_breaker import get_all_states

router  = APIRouter(prefix="/analytics")
logger  = get_logger(__name__)


def _db_query(sql: str, params: tuple = ()) -> List[Any]:
    """Run a read query; return empty list if DB is unavailable."""
    try:
        from app.db.connection import db_available, get_conn
        if not db_available():
            return []
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
    except Exception as exc:
        logger.warning(f"Analytics query failed: {exc}")
        return []


def _since(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


# ── /analytics/overview ───────────────────────────────────────────────────────

@router.get("/overview", summary="High-level KPIs")
async def overview(hours: int = Query(24, ge=1, le=168)):
    since = _since(hours)
    rows = _db_query("""
        SELECT
            COUNT(*)                                   AS total_requests,
            COUNT(*) FILTER (WHERE cache_hit)          AS cache_hits,
            COUNT(*) FILTER (WHERE status_code >= 500) AS errors,
            AVG(duration_ms)                           AS avg_latency_ms,
            PERCENTILE_CONT(0.95) WITHIN GROUP
                (ORDER BY duration_ms)                 AS p95_latency_ms,
            COUNT(*) FILTER (WHERE security_issues_count > 0) AS requests_with_security_issues
        FROM request_metrics
        WHERE created_at >= %s
    """, (since,))

    if not rows or rows[0][0] is None:
        return {"total_requests": 0, "cache_hits": 0, "errors": 0,
                "avg_latency_ms": 0, "p95_latency_ms": 0,
                "cache_hit_rate": 0, "error_rate": 0,
                "requests_with_security_issues": 0}

    r = rows[0]
    total = r[0] or 0
    return {
        "total_requests":               total,
        "cache_hits":                   r[1] or 0,
        "errors":                       r[2] or 0,
        "avg_latency_ms":               round(float(r[3] or 0), 1),
        "p95_latency_ms":               round(float(r[4] or 0), 1),
        "cache_hit_rate":               round((r[1] or 0) / max(total, 1) * 100, 1),
        "error_rate":                   round((r[2] or 0) / max(total, 1) * 100, 1),
        "requests_with_security_issues": r[5] or 0,
    }


# ── /analytics/requests-over-time ────────────────────────────────────────────

@router.get("/requests-over-time", summary="Request volume bucketed by hour")
async def requests_over_time(hours: int = Query(24, ge=1, le=168)):
    since = _since(hours)
    rows = _db_query("""
        SELECT
            DATE_TRUNC('hour', created_at) AS hour,
            COUNT(*)                        AS requests,
            COUNT(*) FILTER (WHERE cache_hit) AS cache_hits,
            COUNT(*) FILTER (WHERE status_code >= 500) AS errors
        FROM request_metrics
        WHERE created_at >= %s
        GROUP BY 1
        ORDER BY 1
    """, (since,))

    return [
        {
            "hour":       row[0].isoformat() if row[0] else None,
            "requests":   row[1],
            "cache_hits": row[2],
            "errors":     row[3],
        }
        for row in rows
    ]


# ── /analytics/providers ─────────────────────────────────────────────────────

@router.get("/providers", summary="Provider usage distribution + circuit breaker states")
async def providers(hours: int = Query(24, ge=1, le=168)):
    since = _since(hours)
    rows = _db_query("""
        SELECT provider_used, COUNT(*) AS requests,
               AVG(duration_ms) AS avg_ms
        FROM request_metrics
        WHERE created_at >= %s AND provider_used IS NOT NULL
        GROUP BY 1
        ORDER BY 2 DESC
    """, (since,))

    cb_states = get_all_states()

    return {
        "usage": [
            {
                "provider":    row[0],
                "requests":    row[1],
                "avg_ms":      round(float(row[2] or 0), 1),
                "cb_state":    cb_states.get(row[0], "CLOSED"),
            }
            for row in rows
        ],
        "circuit_breakers": cb_states,
    }


# ── /analytics/security ───────────────────────────────────────────────────────

@router.get("/security", summary="Security findings aggregated by severity")
async def security_summary(hours: int = Query(24, ge=1, le=168)):
    since = _since(hours)
    rows = _db_query("""
        SELECT
            SUM(security_issues_count)                                    AS total_issues,
            COUNT(*) FILTER (WHERE security_issues_count > 0)             AS affected_reviews,
            COUNT(*)                                                       AS total_reviews
        FROM request_metrics
        WHERE created_at >= %s AND endpoint = '/api/v1/review'
    """, (since,))

    r = rows[0] if rows else (0, 0, 0)
    return {
        "total_issues":     int(r[0] or 0),
        "affected_reviews": int(r[1] or 0),
        "total_reviews":    int(r[2] or 0),
    }


# ── /analytics/latency-percentiles ───────────────────────────────────────────

@router.get("/latency", summary="p50/p95/p99 latency by endpoint")
async def latency(hours: int = Query(24, ge=1, le=168)):
    since = _since(hours)
    rows = _db_query("""
        SELECT
            endpoint,
            PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY duration_ms) AS p50,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95,
            PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY duration_ms) AS p99,
            COUNT(*) AS requests
        FROM request_metrics
        WHERE created_at >= %s
        GROUP BY 1
        ORDER BY 4 DESC
    """, (since,))

    return [
        {
            "endpoint": row[0],
            "p50_ms":   round(float(row[1] or 0), 1),
            "p95_ms":   round(float(row[2] or 0), 1),
            "p99_ms":   round(float(row[3] or 0), 1),
            "requests": row[4],
        }
        for row in rows
    ]


# ── /analytics/languages ──────────────────────────────────────────────────────

@router.get("/languages", summary="Language distribution of reviewed code")
async def languages(hours: int = Query(24, ge=1, le=168)):
    since = _since(hours)
    rows = _db_query("""
        SELECT language, COUNT(*) AS reviews
        FROM request_metrics
        WHERE created_at >= %s AND language IS NOT NULL
        GROUP BY 1
        ORDER BY 2 DESC
    """, (since,))

    return [{"language": row[0], "reviews": row[1]} for row in rows]
