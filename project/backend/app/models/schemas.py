"""
All request/response shapes.  Pydantic v2 with strict validation.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enums ────────────────────────────────────────────────────────────────────

class SupportedLanguage(str, Enum):
    PYTHON = "Python"
    JAVA = "Java"
    JAVASCRIPT = "JavaScript"
    TYPESCRIPT = "TypeScript"
    CPP = "C++"
    GO = "Go"
    RUST = "Rust"
    AUTO = "auto"          # let the classifier decide


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


# ── Requests ─────────────────────────────────────────────────────────────────

class ReviewRequest(BaseModel):
    code: str = Field(..., min_length=1, description="Source code to review")
    language: SupportedLanguage = SupportedLanguage.AUTO

    @field_validator("code")
    @classmethod
    def strip_and_check(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("code must not be empty or whitespace-only")
        return stripped


class ProblemRequest(BaseModel):
    problem: str = Field(..., min_length=10, description="DSA problem statement")
    language: SupportedLanguage = SupportedLanguage.PYTHON

    @field_validator("problem")
    @classmethod
    def strip_and_check(cls, v: str) -> str:
        return v.strip()


# ── Sub-response models ───────────────────────────────────────────────────────

class SecurityIssue(BaseModel):
    rule_id: str
    severity: Severity
    line: Optional[int] = None
    description: str
    recommendation: str


class ComplexityInfo(BaseModel):
    time: str
    space: str
    reasoning: str


class OptimizationChange(BaseModel):
    category: str       # e.g. "algorithmic", "readability", "memory"
    description: str
    impact: str         # e.g. "O(n²) → O(n)"


class DSASolution(BaseModel):
    title: str
    approach: str
    clean_code: str
    commented_code: str
    time_complexity: ComplexityInfo
    space_complexity: ComplexityInfo


# ── Primary responses ─────────────────────────────────────────────────────────

class ReviewResponse(BaseModel):
    request_id: str
    valid: bool
    already_optimal: bool = False
    detected_language: str
    original_code: str
    optimized_code: str
    before_complexity: ComplexityInfo
    after_complexity: ComplexityInfo
    security_issues: List[SecurityIssue] = Field(default_factory=list)
    changes_made: List[OptimizationChange] = Field(default_factory=list)
    explanation: str
    analysis: str
    fallback_used: bool = False
    provider_used: Optional[str] = None
    pipeline_ms: Optional[int] = None


class ProblemResponse(BaseModel):
    request_id: str
    valid: bool
    solutions: List[DSASolution]
    fallback_used: bool = False
    provider_used: Optional[str] = None


class ImageReviewResponse(BaseModel):
    request_id: str
    valid: bool
    detected_type: str          # "code" | "problem"
    extracted_text: str
    review: Optional[ReviewResponse] = None
    problem_solutions: Optional[ProblemResponse] = None


class HealthResponse(BaseModel):
    status: str
    version: str
    providers_available: List[str]
    mock_mode: bool


class ErrorResponse(BaseModel):
    request_id: str
    error: str
    detail: str
