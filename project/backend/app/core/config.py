"""
Central configuration — single source of truth for all env vars.
"""
from functools import lru_cache
from pathlib import Path
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator

BASE_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"), env_file_encoding="utf-8",
        case_sensitive=False, extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────
    APP_NAME: str = "AI Code Reviewer"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = False
    MOCK_MODE: bool = False

    # ── Server ───────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    ALLOWED_ORIGINS: List[str] = ["http://localhost:8501", "http://127.0.0.1:8501"]

    # ── LLM Providers ────────────────────────────────────────
    GOOGLE_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    OPENROUTER_API_KEY: Optional[str] = None
    DEEPSEEK_API_KEY: Optional[str] = None
    MISTRAL_API_KEY: Optional[str] = None

    # ── Model names ──────────────────────────────────────────
    PRIMARY_MODEL: str = "gemini-2.5-pro"
    FALLBACK_MODEL: str = "gemini-2.5-flash"
    GROQ_MODEL: str = "llama3-70b-8192"
    OPENROUTER_MODEL: str = "anthropic/claude-3-haiku"

    # ── LLM Behaviour ────────────────────────────────────────
    LLM_TIMEOUT_SECONDS: int = 60
    LLM_MAX_RETRIES: int = 1
    LLM_TEMPERATURE: float = 0.0

    # ── Input Limits ─────────────────────────────────────────
    MAX_CODE_LENGTH: int = 20_000
    MAX_PROBLEM_LENGTH: int = 5_000
    MAX_IMAGE_SIZE_MB: int = 10

    # ── Redis ────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # ── PostgreSQL + pgvector ─────────────────────────────────
    DATABASE_URL: str = "postgresql://reviewer:reviewer@localhost:5432/code_reviewer"

    # ── Rate Limiting ────────────────────────────────────────
    RATE_LIMIT_FREE_RPM: int = 10
    RATE_LIMIT_PRO_RPM: int = 60
    RATE_LIMIT_ENTERPRISE_RPM: int = 600
    DEFAULT_TENANT_API_KEY: str = "dev-key-local"

    # ── Circuit Breaker ───────────────────────────────────────
    CB_FAILURE_THRESHOLD: int = 5       # failures before OPEN
    CB_RECOVERY_TIMEOUT_S: int = 30     # seconds before HALF-OPEN
    CB_HALF_OPEN_MAX_CALLS: int = 2     # test calls in HALF-OPEN

    # ── Semantic Cache ────────────────────────────────────────
    CACHE_SIMILARITY_THRESHOLD: float = 0.93
    CACHE_TTL_HOURS: int = 24
    EMBEDDING_MODEL: str = "models/embedding-001"

    # ── GitHub ───────────────────────────────────────────────
    GITHUB_WEBHOOK_SECRET: Optional[str] = None
    GITHUB_TOKEN: Optional[str] = None
    GITHUB_MAX_FILES_PER_PR: int = 10
    GITHUB_MAX_FILE_BYTES: int = 50_000

    # ── Logging ──────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {allowed}")
        return upper


@lru_cache
def get_settings() -> Settings:
    return Settings()
