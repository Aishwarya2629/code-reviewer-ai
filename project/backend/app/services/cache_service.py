"""
Semantic cache — avoids re-running the full 6-node pipeline for similar code.

Two-tier lookup:
  Tier 1 (exact): SHA256 hash match — instant, zero embedding cost.
  Tier 2 (semantic): pgvector cosine similarity ≥ CACHE_SIMILARITY_THRESHOLD.

Why semantic instead of just exact?
  Two submissions of the same algorithm with different variable names,
  comments, or minor formatting produce different SHA256 hashes but are
  semantically identical. The embedding captures the structural/semantic
  meaning, not byte-level identity.

Embedding model: Google text-embedding-001 (768 dimensions).
Fallback: if no embedding provider, only exact-match cache works.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.core.metrics import cache_hits_total, cache_misses_total
from app.db.connection import db_available, get_conn

logger = get_logger(__name__)
settings = get_settings()

# ── Embedding helper ──────────────────────────────────────────────────────────

_embedder = None


def _get_embedder():
    global _embedder
    if _embedder is not None:
        return _embedder
    try:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
        if settings.GOOGLE_API_KEY:
            _embedder = GoogleGenerativeAIEmbeddings(
                model=settings.EMBEDDING_MODEL,
                google_api_key=settings.GOOGLE_API_KEY,
            )
            return _embedder
    except Exception as exc:
        logger.warning(f"Embedding model unavailable — semantic cache disabled: {exc}")
    return None


def _embed(code: str) -> Optional[list]:
    embedder = _get_embedder()
    if embedder is None:
        return None
    try:
        return embedder.embed_query(code[:8000])
    except Exception as exc:
        logger.warning(f"Embedding failed: {exc}")
        return None


def _sha256(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


# ── Cache lookup ──────────────────────────────────────────────────────────────

def lookup(code: str, language: str) -> Optional[Dict[str, Any]]:
    """
    Return cached review result or None.
    Updates hit counter and logs tier (exact/semantic).
    """
    if not db_available():
        return None

    code_hash = _sha256(code)
    now = datetime.now(timezone.utc)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Tier 1: exact hash match
                cur.execute("""
                    SELECT id, result FROM review_cache
                    WHERE code_hash = %s AND language = %s AND expires_at > %s
                    LIMIT 1
                """, (code_hash, language, now))
                row = cur.fetchone()
                if row:
                    _record_hit(cur, row[0], "exact")
                    cache_hits_total.inc()
                    logger.info(f"Cache HIT (exact) hash={code_hash[:12]}")
                    return json.loads(row[1]) if isinstance(row[1], str) else row[1]

                # Tier 2: semantic similarity
                embedding = _embed(code)
                if embedding is None:
                    cache_misses_total.inc()
                    return None

                vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
                cur.execute("""
                    SELECT id, result,
                           1 - (code_embedding <=> %s::vector) AS similarity
                    FROM review_cache
                    WHERE language = %s AND expires_at > %s
                      AND code_embedding IS NOT NULL
                    ORDER BY code_embedding <=> %s::vector
                    LIMIT 1
                """, (vec_str, language, now, vec_str))
                row = cur.fetchone()

                if row and row[2] >= settings.CACHE_SIMILARITY_THRESHOLD:
                    _record_hit(cur, row[0], "semantic")
                    cache_hits_total.inc()
                    logger.info(f"Cache HIT (semantic) similarity={row[2]:.3f}")
                    return json.loads(row[1]) if isinstance(row[1], str) else row[1]

    except Exception as exc:
        logger.warning(f"Cache lookup failed (non-fatal): {exc}")

    cache_misses_total.inc()
    return None


def store(code: str, language: str, result: Dict[str, Any], tenant_id: Optional[str] = None) -> None:
    """Store a review result in the cache asynchronously (best-effort)."""
    if not db_available():
        return

    code_hash = _sha256(code)
    embedding = _embed(code)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.CACHE_TTL_HOURS)

    try:
        vec_str = ("[" + ",".join(str(x) for x in embedding) + "]") if embedding else None
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO review_cache
                        (code_hash, code_embedding, language, result, tenant_id, expires_at)
                    VALUES (%s, %s::vector, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (
                    code_hash,
                    vec_str,
                    language,
                    json.dumps(result),
                    tenant_id,
                    expires_at,
                ))
        logger.info(f"Cache stored hash={code_hash[:12]} semantic={'yes' if embedding else 'no'}")
    except Exception as exc:
        logger.warning(f"Cache store failed (non-fatal): {exc}")


def _record_hit(cur, cache_id, tier: str) -> None:
    try:
        cur.execute("UPDATE review_cache SET hits = hits + 1 WHERE id = %s", (cache_id,))
    except Exception:
        pass
