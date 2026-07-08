"""
Complexity analyzer — establishes the BEFORE baseline.

This runs on the original code so the optimizer can compare against it.
We also do a lightweight AST-based heuristic as a cross-check / fallback.
"""
from __future__ import annotations

import ast
from typing import Tuple

from app.agents.state import AgentState
from app.core.logging_config import get_logger
from app.prompts.templates import COMPLEXITY_PROMPT
from app.services.llm_service import safe_invoke, parse_json_response

logger = get_logger(__name__)


def _ast_heuristic(code: str, language: str) -> Tuple[str, str]:
    """
    Very rough Python-only heuristic via AST nesting depth.
    Returns ("O(?)", "O(?)") for non-Python code.
    Used only when LLM fails.
    """
    if language.lower() != "python":
        return "O(?)", "O(?)"
    try:
        tree = ast.parse(code)
        max_depth = 0
        for node in ast.walk(tree):
            depth = sum(1 for _ in ast.walk(node)
                        if isinstance(_, (ast.For, ast.While, ast.ListComp, ast.GeneratorExp)))
            max_depth = max(max_depth, depth)

        time_guess = {0: "O(1)", 1: "O(n)", 2: "O(n²)", 3: "O(n³)"}.get(max_depth, "O(n^k)")
        return time_guess, "O(n)"
    except SyntaxError:
        return "O(?)", "O(?)"


def complexity_analyzer_node(state: AgentState) -> AgentState:
    code = state.get("raw_code", "")
    language = state.get("detected_language", "unknown")
    nodes_executed = list(state.get("nodes_executed", []))
    nodes_executed.append("complexity_analyzer")

    try:
        prompt = COMPLEXITY_PROMPT.format(language=language, code=code[:8000])
        result = safe_invoke(prompt)
        parsed = parse_json_response(result.content)

        time_c = parsed.get("time_complexity", "O(?)")
        space_c = parsed.get("space_complexity", "O(?)")
        reasoning = parsed.get("reasoning", "")
        logger.info(f"Complexity: time={time_c} space={space_c}")

        return {
            **state,
            "before_time": time_c,
            "before_space": space_c,
            "complexity_reasoning": reasoning,
            "provider_used": state.get("provider_used") or result.provider_used,
            "fallback_used": state.get("fallback_used", False) or result.fallback_used,
            "nodes_executed": nodes_executed,
        }

    except Exception as exc:
        logger.warning(f"Complexity LLM failed, using heuristic: {exc}")
        time_h, space_h = _ast_heuristic(code, language)
        return {
            **state,
            "before_time": time_h,
            "before_space": space_h,
            "complexity_reasoning": "Estimated via static heuristic (LLM unavailable).",
            "nodes_executed": nodes_executed,
        }
