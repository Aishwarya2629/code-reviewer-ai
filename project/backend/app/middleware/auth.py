"""
Auth middleware — resolves X-API-Key header to a tenant object.

Multi-tenancy design:
  - Every request carries X-API-Key (or falls back to DEFAULT_TENANT_API_KEY for dev)
  - Tenant record (tier, rate_limit_rpm) is looked up from DB, cached in Redis for 5 min
  - Tenant info attached to request.state so routes and rate limiter can read it

Tier → RPM mapping:
  free:       10 req/min
  pro:        60 req/min
  enterprise: 600 req/min
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.core.metrics import tenant_requests_total

logger = get_logger(__name__)
settings = get_settings()

_TIER_RPM = {
    "free":       settings.RATE_LIMIT_FREE_RPM,
    "pro":        settings.RATE_LIMIT_PRO_RPM,
    "enterprise": settings.RATE_LIMIT_ENTERPRISE_RPM,
}

# Default tenant used when no X-API-Key header is present (local dev)
_DEFAULT_TENANT = {
    "api_key": settings.DEFAULT_TENANT_API_KEY,
    "name":    "Local Dev",
    "tier":    "enterprise",
    "rpm":     settings.RATE_LIMIT_ENTERPRISE_RPM,
}


def _get_redis():
    try:
        import redis as redislib
        r = redislib.from_url(settings.REDIS_URL, decode_responses=True, socket_timeout=1)
        r.ping()
        return r
    except Exception:
        return None


def _lookup_tenant_db(api_key: str) -> Optional[dict]:
    """Look up tenant from PostgreSQL."""
    try:
        from app.db.connection import db_available, get_conn
        if not db_available():
            return None
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT api_key, name, tier, rate_limit_rpm FROM tenants WHERE api_key = %s",
                    (api_key,)
                )
                row = cur.fetchone()
                if row:
                    return {"api_key": row[0], "name": row[1], "tier": row[2], "rpm": row[3]}
    except Exception as exc:
        logger.warning(f"Tenant DB lookup failed: {exc}")
    return None


def resolve_tenant(api_key: str) -> dict:
    """
    Resolve API key → tenant dict.
    Cache result in Redis for 5 minutes to avoid a DB hit per request.
    """
    if not api_key or api_key == settings.DEFAULT_TENANT_API_KEY:
        return _DEFAULT_TENANT

    # Redis cache
    r = _get_redis()
    if r:
        cached = r.get(f"tenant:{api_key}")
        if cached:
            return json.loads(cached)

    tenant = _lookup_tenant_db(api_key)

    if tenant is None:
        # Unknown key → treat as free tier
        tenant = {
            "api_key": api_key,
            "name":    "unknown",
            "tier":    "free",
            "rpm":     settings.RATE_LIMIT_FREE_RPM,
        }

    if r:
        r.setex(f"tenant:{api_key}", 300, json.dumps(tenant))  # 5-min TTL

    return tenant


class TenantMiddleware(BaseHTTPMiddleware):
    """
    Attaches resolved tenant to request.state.tenant.
    Runs before the rate limiter so the rate limiter can read rpm from tenant.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        api_key = (
            request.headers.get("X-API-Key")
            or request.headers.get("x-api-key")
            or settings.DEFAULT_TENANT_API_KEY
        )
        tenant = resolve_tenant(api_key)
        request.state.tenant = tenant

        # Record tenant metric
        tenant_requests_total.labels(
            tenant=tenant.get("name", "unknown"),
            tier=tenant.get("tier", "free"),
        ).inc()

        return await call_next(request)
