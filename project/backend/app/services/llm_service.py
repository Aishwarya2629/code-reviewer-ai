"""
LLM service — provider fallback chain with circuit breaker + Prometheus metrics.

Flow per call:
  for each provider:
    1. Check circuit breaker — skip if OPEN
    2. Invoke LLM
    3a. Success → record_success(), emit metrics, return
    3b. Failure → record_failure(), log, back-off, try next provider
  All failed → LLMUnavailableError (503)
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.core.exceptions import LLMUnavailableError
from app.core.metrics import llm_requests_total, llm_latency_seconds

logger = get_logger(__name__)
settings = get_settings()


# ── Lazy circuit breaker import (Redis may not be available) ──────────────────
def _cb_allowed(name: str) -> bool:
    try:
        from app.services.circuit_breaker import is_call_allowed
        return is_call_allowed(name)
    except Exception:
        return True


def _cb_success(name: str) -> None:
    try:
        from app.services.circuit_breaker import record_success
        record_success(name)
    except Exception:
        pass


def _cb_failure(name: str) -> None:
    try:
        from app.services.circuit_breaker import record_failure
        record_failure(name)
    except Exception:
        pass


# ── Provider registry ─────────────────────────────────────────────────────────

def _build_providers() -> List[Tuple[str, BaseChatModel]]:
    providers: List[Tuple[str, BaseChatModel]] = []

    if settings.GOOGLE_API_KEY:
        base = dict(
            google_api_key=settings.GOOGLE_API_KEY,
            temperature=settings.LLM_TEMPERATURE,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            max_retries=settings.LLM_MAX_RETRIES,
        )
        providers.append(("gemini-primary", ChatGoogleGenerativeAI(model=settings.PRIMARY_MODEL, **base)))
        providers.append(("gemini-flash",   ChatGoogleGenerativeAI(model=settings.FALLBACK_MODEL, **base)))

    if settings.GROQ_API_KEY:
        providers.append(("groq", ChatGroq(
            model=settings.GROQ_MODEL,
            api_key=settings.GROQ_API_KEY,
            temperature=settings.LLM_TEMPERATURE,
            max_retries=settings.LLM_MAX_RETRIES,
        )))

    if settings.OPENROUTER_API_KEY:
        providers.append(("openrouter", ChatOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.OPENROUTER_API_KEY,
            model=settings.OPENROUTER_MODEL,
            temperature=settings.LLM_TEMPERATURE,
            max_retries=settings.LLM_MAX_RETRIES,
        )))

    if settings.DEEPSEEK_API_KEY:
        providers.append(("deepseek", ChatOpenAI(
            base_url="https://api.deepseek.com/v1",
            api_key=settings.DEEPSEEK_API_KEY,
            model="deepseek-coder",
            temperature=settings.LLM_TEMPERATURE,
        )))

    if settings.MISTRAL_API_KEY:
        providers.append(("mistral", ChatOpenAI(
            base_url="https://api.mistral.ai/v1",
            api_key=settings.MISTRAL_API_KEY,
            model="codestral-latest",
            temperature=settings.LLM_TEMPERATURE,
        )))

    return providers


_PROVIDERS: List[Tuple[str, BaseChatModel]] = _build_providers()


# ── Result value object ───────────────────────────────────────────────────────

class LLMResult:
    __slots__ = ("content", "provider_used", "latency_ms", "fallback_used", "mock")

    def __init__(self, content: str, provider_used: str,
                 latency_ms: int, fallback_used: bool, mock: bool):
        self.content       = content
        self.provider_used = provider_used
        self.latency_ms    = latency_ms
        self.fallback_used = fallback_used
        self.mock          = mock


# ── Core invocation ───────────────────────────────────────────────────────────

def safe_invoke(prompt: str, mock_fn: Optional[Callable[[], Any]] = None) -> LLMResult:
    """
    Invoke LLM with circuit-breaker gating and provider failover.
    Records Prometheus metrics on every attempt.
    """
    if settings.MOCK_MODE:
        payload = json.dumps(mock_fn()) if mock_fn else "{}"
        return LLMResult(payload, "mock", 0, True, True)

    if not _PROVIDERS:
        if mock_fn:
            return LLMResult(json.dumps(mock_fn()), "mock", 0, True, True)
        raise LLMUnavailableError()

    last_error: Optional[Exception] = None

    for idx, (name, llm) in enumerate(_PROVIDERS):

        # ── Circuit breaker gate ──────────────────────────────
        if not _cb_allowed(name):
            logger.info(f"CircuitBreaker OPEN for provider={name} — skipping")
            llm_requests_total.labels(provider=name, status="circuit_open").inc()
            continue

        try:
            logger.info(f"Invoking provider={name}")
            t0 = time.perf_counter()
            response = llm.invoke(prompt)
            latency_ms = int((time.perf_counter() - t0) * 1000)

            content = response.content if hasattr(response, "content") else str(response)
            if isinstance(content, list):
                content = "\n".join(str(x) for x in content)

            # ── Record success ────────────────────────────────
            _cb_success(name)
            llm_requests_total.labels(provider=name, status="success").inc()
            llm_latency_seconds.labels(provider=name).observe(latency_ms / 1000)

            logger.info(f"Provider={name} OK latency_ms={latency_ms}")
            return LLMResult(
                content=str(content),
                provider_used=name,
                latency_ms=latency_ms,
                fallback_used=(idx > 0),
                mock=False,
            )

        except Exception as exc:
            last_error = exc
            # ── Record failure ────────────────────────────────
            _cb_failure(name)
            llm_requests_total.labels(provider=name, status="failure").inc()
            logger.warning(f"Provider={name} failed error={exc!r}")
            time.sleep(min(0.5 * (idx + 1), 2.0))

    logger.error(f"All {len(_PROVIDERS)} providers failed last_error={last_error!r}")
    if mock_fn:
        return LLMResult(json.dumps(mock_fn()), "mock", 0, True, True)
    raise LLMUnavailableError()


# ── JSON parsing ──────────────────────────────────────────────────────────────

def parse_json_response(content: str) -> Dict[str, Any]:
    content = content.strip()
    content = re.sub(r"```(?:json)?", "", content).replace("```", "")
    content = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", content)
    start = content.find("{")
    end   = content.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in LLM response")
    return json.loads(content[start: end + 1])


def available_providers() -> List[str]:
    return [name for name, _ in _PROVIDERS] or (["mock"] if settings.MOCK_MODE else [])
