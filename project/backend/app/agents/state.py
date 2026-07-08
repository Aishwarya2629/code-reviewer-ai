"""
LangGraph state — typed dict shared across all pipeline nodes.

Design note: we keep everything in one flat TypedDict because LangGraph
merges partial updates from each node. Nested dicts cause subtle merge issues.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class SecurityFinding(TypedDict):
    rule_id: str
    severity: str      # CRITICAL | HIGH | MEDIUM | LOW | INFO
    line: Optional[int]
    description: str
    recommendation: str


class AgentState(TypedDict, total=False):
    # ── Inputs (set before graph.invoke) ─────────────────────
    raw_code: str
    requested_language: str        # user-supplied, may be "auto"
    request_id: str

    # ── Classifier outputs ────────────────────────────────────
    detected_language: str         # e.g. "Python"
    input_type: str                # "code" | "problem" | "invalid"
    classifier_confidence: float   # 0.0 – 1.0

    # ── Security scanner outputs ──────────────────────────────
    security_findings: List[SecurityFinding]
    has_critical_security: bool

    # ── Complexity analyzer outputs ───────────────────────────
    before_time: str
    before_space: str
    complexity_reasoning: str

    # ── Optimizer outputs ─────────────────────────────────────
    optimized_code: str
    already_optimal: bool
    changes_made: List[Dict[str, str]]   # [{category, description, impact}]

    # ── Validator outputs ─────────────────────────────────────
    optimization_valid: bool
    validator_notes: str

    # ── Explainer outputs ─────────────────────────────────────
    after_time: str
    after_space: str
    analysis: str
    explanation: str

    # ── Pipeline metadata ─────────────────────────────────────
    provider_used: str
    fallback_used: bool
    pipeline_error: Optional[str]
    nodes_executed: List[str]
