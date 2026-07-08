"""
Rate limiting — Redis sliding window counter, per-tenant.

Algorithm: Sliding window log using a Redis sorted set.
  Key: ratelimit:{tenant_api_key}
  Members: request timestamps (as score + member)
  On each request:
    1. Remove entries older than window_seconds
    2. Count remaining entries
    3. If count >= limit → 429
    4. Else add current timestamp, set TTL

Why sliding window over fixed window?
  Fixed window allows 2× burst at window boundaries (last second of
  window N + first second of window N+1). Sliding window prevents this.
  The sorted set approach is O(log N) per request — acceptable overhead.

Why per-tenant (not per-IP)?
  IP-based rate limiting breaks behind NAT/proxies (whole office gets
  rate-limited because one user is spamming). API-key-based is precise.
"""
from __future__ import annotations

import time
import uuid
from typing import Optional

import redis

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.core.metrics import rate_limit_hits_total

logger = get_logger(__name__)
settings = get_settings()

_redis: Optional[redis.Redis] = None


def _get_redis() -> Optional[redis.Redis]:
    global _redis
    if _redis is not None:
        return _redis
    try:
        _redis = redis.from_url(settings.REDIS_URL, decode_responses=True, socket_timeout=1)
        _redis.ping()
        return _redis
    except Exception as exc:
        logger.warning(f"Redis unavailable for rate limiting — no rate limiting applied: {exc}")
        return None


class RateLimitExceeded(Exception):
    def __init__(self, tenant: str, limit: int, window_s: int):
        self.tenant = tenant
        self.limit = limit
        self.window_s = window_s
        super().__init__(f"Rate limit exceeded for tenant={tenant}: {limit} req/{window_s}s")


def check_rate_limit(tenant_api_key: str, rpm: int) -> None:
    """
    Raises RateLimitExceeded if the tenant has exceeded their RPM quota.
    Silently passes if Redis is unavailable (fail-open).
    """
    r = _get_redis()
    if r is None:
        return   # Redis down → fail open, don't block requests

    window_s = 60
    key = f"ratelimit:{tenant_api_key}"
    now = time.time()
    window_start = now - window_s

    pipe = r.pipeline()
    # 1. Remove requests outside the sliding window
    pipe.zremrangebyscore(key, 0, window_start)
    # 2. Count current requests in window
    pipe.zcard(key)
    # 3. Add this request
    pipe.zadd(key, {str(uuid.uuid4()): now})
    # 4. Set expiry (window_s + buffer)
    pipe.expire(key, window_s + 5)
    results = pipe.execute()

    current_count = results[1]   # before adding this request

    if current_count >= rpm:
        rate_limit_hits_total.labels(tenant=tenant_api_key[:12]).inc()
        raise RateLimitExceeded(tenant_api_key, rpm, window_s)


def get_remaining(tenant_api_key: str, rpm: int) -> int:
    """Return remaining requests in the current window."""
    r = _get_redis()
    if r is None:
        return rpm

    key = f"ratelimit:{tenant_api_key}"
    now = time.time()
    window_start = now - 60
    r.zremrangebyscore(key, 0, window_start)
    used = r.zcard(key)
    return max(0, rpm - used)
