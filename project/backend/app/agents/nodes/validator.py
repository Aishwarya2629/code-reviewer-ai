"""
Validator node — sanity-checks the optimizer's output.

A second LLM call that asks: "does this actually make sense?"
Catches cases where the optimizer changed the algorithm logic incorrectly.
"""
from __future__ import annotations

from app.agents.state import AgentState
from app.core.logging_config import get_logger
from app.prompts.templates import VALIDATOR_PROMPT
from app.services.llm_service import safe_invoke, parse_json_response

logger = get_logger(__name__)


def validator_node(state: AgentState) -> AgentState:
    original = state.get("raw_code", "")
    optimized = state.get("optimized_code", original)
    language = state.get("detected_language", "Python")
    changes = state.get("changes_made", [])
    nodes_executed = list(state.get("nodes_executed", []))
    nodes_executed.append("validator")

    # Skip validation if nothing changed
    if original.strip() == optimized.strip() or state.get("already_optimal"):
        return {
            **state,
            "optimization_valid": True,
            "validator_notes": "No changes to validate.",
            "nodes_executed": nodes_executed,
        }

    changes_summary = "\n".join(
        f"- [{c.get('category')}] {c.get('description')} → {c.get('impact')}"
        for c in changes[:5]
    ) or "No changes listed."

    try:
        prompt = VALIDATOR_PROMPT.format(
            language=language,
            original_code=original[:5000],
            optimized_code=optimized[:5000],
            changes_summary=changes_summary,
        )
        result = safe_invoke(prompt)
        parsed = parse_json_response(result.content)
        valid = bool(parsed.get("valid", True))
        notes = parsed.get("notes", "LGTM")
        logger.info(f"Validator: valid={valid} notes={notes[:80]}")

        if not valid:
            # Rollback: revert to original code if validation fails
            logger.warning("Validation failed — reverting to original code")
            return {
                **state,
                "optimized_code": original,
                "changes_made": [],
                "already_optimal": True,
                "optimization_valid": False,
                "validator_notes": notes,
                "nodes_executed": nodes_executed,
            }

        return {
            **state,
            "optimization_valid": True,
            "validator_notes": notes,
            "nodes_executed": nodes_executed,
        }

    except Exception as exc:
        logger.warning(f"Validator failed (non-fatal, keeping optimization): {exc}")
        return {
            **state,
            "optimization_valid": True,
            "validator_notes": "Validation skipped (LLM unavailable).",
            "nodes_executed": nodes_executed,
        }
