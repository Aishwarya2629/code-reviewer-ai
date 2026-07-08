"""
Circuit Breaker — per-provider state machine stored in Redis.

States:
  CLOSED     → Normal operation. Failures are counted.
  OPEN       → Provider is down. All calls fail immediately (no network).
               After CB_RECOVERY_TIMEOUT_S, transitions to HALF-OPEN.
  HALF-OPEN  → Probe state. Allows CB_HALF_OPEN_MAX_CALLS through.
               Success → CLOSED. Failure → OPEN again.

Why Redis (not in-memory)?
  In-memory state is per-process. With multiple uvicorn workers or Celery
  workers, each has its own counters — you'd need N×threshold failures before
  any single process opens. Redis gives a single shared view across all workers.

Interview note: This is a textbook Hystrix-style circuit breaker. The key
insight is that failing FAST (returning immediately when OPEN) protects both
your service and the upstream provider — no piling up of connections waiting
to time out.
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Optional

import redis

from app.core.config import get_settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)
settings = get_settings()

# ── Redis client (lazy, shared across module) ─────────────────────────────────
_redis: Optional[redis.Redis] = None


def _get_redis() -> Optional[redis.Redis]:
    global _redis
    if _redis is not None:
        return _redis
    try:
        _redis = redis.from_url(settings.REDIS_URL, decode_responses=True, socket_timeout=2)
        _redis.ping()
        return _redis
    except Exception as exc:
        logger.warning(f"Redis unavailable for circuit breaker — running without CB: {exc}")
        return None


# ── State enum ────────────────────────────────────────────────────────────────

class CBState(str, Enum):
    CLOSED    = "CLOSED"
    OPEN      = "OPEN"
    HALF_OPEN = "HALF_OPEN"


# ── Redis key helpers ─────────────────────────────────────────────────────────

def _key(provider: str, suffix: str) -> str:
    return f"cb:{provider}:{suffix}"


# ── Public API ────────────────────────────────────────────────────────────────

def get_state(provider: str) -> CBState:
    r = _get_redis()
    if r is None:
        return CBState.CLOSED   # fail-open if Redis is down

    raw = r.get(_key(provider, "state"))
    if raw is None:
        return CBState.CLOSED

    state = CBState(raw)

    # Auto-transition OPEN → HALF_OPEN after recovery timeout
    if state == CBState.OPEN:
        opened_at = r.get(_key(provider, "opened_at"))
        if opened_at and time.time() - float(opened_at) >= settings.CB_RECOVERY_TIMEOUT_S:
            _set_state(provider, CBState.HALF_OPEN, r)
            r.set(_key(provider, "half_open_calls"), 0)
            logger.info(f"CircuitBreaker provider={provider} OPEN→HALF_OPEN")
            return CBState.HALF_OPEN

    return state


def record_success(provider: str) -> None:
    r = _get_redis()
    if r is None:
        return

    state = get_state(provider)
    if state in (CBState.HALF_OPEN, CBState.OPEN):
        logger.info(f"CircuitBreaker provider={provider} {state}→CLOSED (success)")
        _set_state(provider, CBState.CLOSED, r)
        r.delete(_key(provider, "failures"))
        r.delete(_key(provider, "opened_at"))
        r.delete(_key(provider, "half_open_calls"))
    elif state == CBState.CLOSED:
        # Reset failure counter on success
        r.delete(_key(provider, "failures"))


def record_failure(provider: str) -> None:
    r = _get_redis()
    if r is None:
        return

    state = get_state(provider)

    if state == CBState.HALF_OPEN:
        # Any failure in HALF_OPEN → back to OPEN
        logger.warning(f"CircuitBreaker provider={provider} HALF_OPEN→OPEN (probe failed)")
        _open_circuit(provider, r)
        return

    # CLOSED: increment failure counter
    failures_key = _key(provider, "failures")
    failures = r.incr(failures_key)
    r.expire(failures_key, 60)   # rolling 60-second window

    logger.info(f"CircuitBreaker provider={provider} failures={failures}/{settings.CB_FAILURE_THRESHOLD}")

    if failures >= settings.CB_FAILURE_THRESHOLD:
        logger.warning(f"CircuitBreaker provider={provider} CLOSED→OPEN (threshold reached)")
        _open_circuit(provider, r)


def is_call_allowed(provider: str) -> bool:
    """Return False if the circuit is OPEN and the call should be skipped."""
    state = get_state(provider)

    if state == CBState.CLOSED:
        return True

    if state == CBState.OPEN:
        return False

    if state == CBState.HALF_OPEN:
        r = _get_redis()
        if r is None:
            return True
        calls = r.incr(_key(provider, "half_open_calls"))
        allowed = calls <= settings.CB_HALF_OPEN_MAX_CALLS
        if not allowed:
            logger.debug(f"CircuitBreaker provider={provider} HALF_OPEN probe quota exhausted")
        return allowed

    return True


def get_all_states() -> dict:
    """Return state snapshot of all known providers — used by /health and /metrics."""
    r = _get_redis()
    if r is None:
        return {}
    keys = r.keys("cb:*:state")
    result = {}
    for key in keys:
        provider = key.split(":")[1]
        result[provider] = get_state(provider).value
    return result


# ── Internal helpers ──────────────────────────────────────────────────────────

def _set_state(provider: str, state: CBState, r: redis.Redis) -> None:
    r.set(_key(provider, "state"), state.value)


def _open_circuit(provider: str, r: redis.Redis) -> None:
    _set_state(provider, CBState.OPEN, r)
    r.set(_key(provider, "opened_at"), str(time.time()))
    r.delete(_key(provider, "failures"))
