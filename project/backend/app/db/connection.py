"""
Database connection — PostgreSQL with pgvector extension.

Connection pool managed by psycopg2 via a simple pool wrapper.
We keep it dependency-light (no SQLAlchemy) — raw psycopg2 gives
full control and is easier to reason about for connection issues.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Generator, Optional

import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2.extras import RealDictCursor

from app.core.config import get_settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)
settings = get_settings()

_pool: Optional[pg_pool.ThreadedConnectionPool] = None


def init_db() -> None:
    """Create connection pool + run schema migrations. Called at app startup."""
    global _pool
    try:
        _pool = pg_pool.ThreadedConnectionPool(
            minconn=2, maxconn=10,
            dsn=settings.DATABASE_URL,
        )
        _run_migrations()
        logger.info("Database pool initialised")
    except Exception as exc:
        logger.error(f"Database init failed: {exc}. Semantic cache and analytics disabled.")
        _pool = None


@contextmanager
def get_conn() -> Generator[Any, None, None]:
    """Context manager that checks out a connection from the pool."""
    if _pool is None:
        raise RuntimeError("Database pool not initialised")
    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


def db_available() -> bool:
    return _pool is not None


def _run_migrations() -> None:
    """Idempotent schema setup."""
    ddl = """
    -- Enable pgvector
    CREATE EXTENSION IF NOT EXISTS vector;

    -- Tenants (multi-tenancy)
    CREATE TABLE IF NOT EXISTS tenants (
        id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        api_key    VARCHAR(64) UNIQUE NOT NULL,
        name       VARCHAR(100) NOT NULL,
        tier       VARCHAR(20)  NOT NULL DEFAULT 'free',
        rate_limit_rpm INTEGER NOT NULL DEFAULT 10,
        created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    );

    -- Insert default dev tenant (idempotent)
    INSERT INTO tenants (api_key, name, tier, rate_limit_rpm)
    VALUES ('dev-key-local', 'Local Dev', 'enterprise', 600)
    ON CONFLICT (api_key) DO NOTHING;

    -- Semantic review cache
    CREATE TABLE IF NOT EXISTS review_cache (
        id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        code_hash      VARCHAR(64) NOT NULL,
        code_embedding vector(768),
        language       VARCHAR(50),
        result         JSONB NOT NULL,
        tenant_id      UUID REFERENCES tenants(id),
        created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at     TIMESTAMPTZ NOT NULL,
        hits           INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS review_cache_hash_idx
        ON review_cache (code_hash);
    CREATE INDEX IF NOT EXISTS review_cache_embedding_idx
        ON review_cache USING ivfflat (code_embedding vector_cosine_ops)
        WITH (lists = 50);

    -- Request metrics store
    CREATE TABLE IF NOT EXISTS request_metrics (
        id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        request_id           VARCHAR(36) NOT NULL,
        tenant_id            UUID REFERENCES tenants(id),
        endpoint             VARCHAR(100),
        method               VARCHAR(10),
        status_code          INTEGER,
        duration_ms          INTEGER,
        provider_used        VARCHAR(50),
        cache_hit            BOOLEAN NOT NULL DEFAULT FALSE,
        security_issues_count INTEGER NOT NULL DEFAULT 0,
        language             VARCHAR(50),
        created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS request_metrics_tenant_idx
        ON request_metrics (tenant_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS request_metrics_created_idx
        ON request_metrics (created_at DESC);
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
    logger.info("Schema migrations applied")
