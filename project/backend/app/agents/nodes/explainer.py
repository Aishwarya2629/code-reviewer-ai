"""
Explainer node — generates the human-readable output.

Runs LAST so it has the full picture: before/after complexity, security
findings, validator notes, and the actual changes list.
"""
from __future__ import annotations

from app.agents.state import AgentState
from app.core.logging_config import get_logger
from app.prompts.templates import EXPLAINER_PROMPT, COMPLEXITY_PROMPT
from app.services.llm_service import safe_invoke, parse_json_response

logger = get_logger(__name__)


def _security_summary(state: AgentState) -> str:
    findings = state.get("security_findings", [])
    if not findings:
        return "None"
    return "; ".join(f"{f['rule_id']} ({f['severity']})" for f in findings[:5])


def explainer_node(state: AgentState) -> AgentState:
    language = state.get("detected_language", "Python")
    before_time = state.get("before_time", "O(?)")
    before_space = state.get("before_space", "O(?)")
    changes = state.get("changes_made", [])
    already_optimal = state.get("already_optimal", False)
    nodes_executed = list(state.get("nodes_executed", []))
    nodes_executed.append("explainer")

    changes_summary = "\n".join(
        f"- [{c.get('category')}] {c.get('description')} → {c.get('impact')}"
        for c in changes
    ) or "No changes made."
    security_summary = _security_summary(state)

    # Get AFTER complexity via separate LLM call on the optimized code
    after_time, after_space = before_time, before_space
    try:
        opt_code = state.get("optimized_code", "")
        if opt_code and not already_optimal:
            c_result = safe_invoke(
                COMPLEXITY_PROMPT.format(language=language, code=opt_code[:5000])
            )
            c_parsed = parse_json_response(c_result.content)
            after_time = c_parsed.get("time_complexity", before_time)
            after_space = c_parsed.get("space_complexity", before_space)
    except Exception:
        pass   # Keep before values — non-fatal

    try:
        prompt = EXPLAINER_PROMPT.format(
            language=language,
            before_time=before_time,
            before_space=before_space,
            after_time=after_time,
            after_space=after_space,
            already_optimal=already_optimal,
            changes_summary=changes_summary,
            security_summary=security_summary,
        )
        result = safe_invoke(prompt)
        parsed = parse_json_response(result.content)

        analysis = parsed.get("analysis", "")
        explanation = parsed.get("explanation", "")

    except Exception as exc:
        logger.warning(f"Explainer LLM failed: {exc}")
        if already_optimal:
            analysis = "The original code is already algorithmically optimal."
            explanation = "No changes were needed. The complexity and structure are sound."
        else:
            analysis = "Code was optimised based on the changes listed."
            explanation = changes_summary

    return {
        **state,
        "after_time": after_time,
        "after_space": after_space,
        "analysis": analysis,
        "explanation": explanation,
        "nodes_executed": nodes_executed,
    }
