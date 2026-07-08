"""
Optimizer node — the core transformation step.

Receives full context from previous nodes (language, complexity, security issues)
so the prompt is highly informed and avoids false optimisations.
"""
from __future__ import annotations

from typing import Dict, List

from app.agents.state import AgentState
from app.core.logging_config import get_logger
from app.prompts.templates import OPTIMIZER_PROMPT
from app.services.llm_service import safe_invoke, parse_json_response

logger = get_logger(__name__)


def _security_summary(state: AgentState) -> str:
    findings = state.get("security_findings", [])
    if not findings:
        return "None"
    return "; ".join(
        f"{f['rule_id']} ({f['severity']}): {f['description'][:60]}"
        for f in findings[:5]
    )


def _mock_response(code: str) -> Dict:
    return {
        "valid": True,
        "already_optimal": False,
        "optimized_code": code,
        "changes_made": [{"category": "readability",
                          "description": "Minor style improvements",
                          "impact": "No complexity change"}],
        "reason_if_invalid": None,
    }


def optimizer_node(state: AgentState) -> AgentState:
    code = state.get("raw_code", "")
    language = state.get("detected_language", "Python")
    before_time = state.get("before_time", "O(?)")
    before_space = state.get("before_space", "O(?)")
    nodes_executed = list(state.get("nodes_executed", []))
    nodes_executed.append("optimizer")

    security_summary = _security_summary(state)

    try:
        prompt = OPTIMIZER_PROMPT.format(
            language=language,
            before_time=before_time,
            before_space=before_space,
            security_summary=security_summary,
            code=code[:15000],
        )
        result = safe_invoke(prompt, lambda: _mock_response(code))
        parsed = parse_json_response(result.content)

        if not parsed.get("valid", True):
            # Code is invalid — mark and pass through
            return {
                **state,
                "input_type": "invalid",
                "pipeline_error": parsed.get("reason_if_invalid", "Invalid code"),
                "optimized_code": code,
                "already_optimal": False,
                "changes_made": [],
                "nodes_executed": nodes_executed,
            }

        changes: List[Dict] = parsed.get("changes_made", [])
        logger.info(
            f"Optimizer: already_optimal={parsed.get('already_optimal')} "
            f"changes={len(changes)}"
        )

        return {
            **state,
            "optimized_code": parsed.get("optimized_code", code),
            "already_optimal": bool(parsed.get("already_optimal", False)),
            "changes_made": changes,
            "provider_used": state.get("provider_used") or result.provider_used,
            "fallback_used": state.get("fallback_used", False) or result.fallback_used,
            "nodes_executed": nodes_executed,
        }

    except Exception as exc:
        logger.error(f"Optimizer node failed: {exc}")
        return {
            **state,
            "optimized_code": code,
            "already_optimal": True,
            "changes_made": [],
            "pipeline_error": f"Optimizer unavailable: {exc}",
            "nodes_executed": nodes_executed,
        }
