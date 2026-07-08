"""
Classifier node — detects language and input type.
This is the entry gate: if input is invalid, the pipeline short-circuits here.
"""
from __future__ import annotations

from app.agents.state import AgentState
from app.core.logging_config import get_logger
from app.prompts.templates import CLASSIFIER_PROMPT
from app.services.llm_service import safe_invoke, parse_json_response

logger = get_logger(__name__)

# Maps code signal strings to language names (for heuristic fallback)
_FALLBACK_MAP = [
    ("def ", "Python"),
    ("import ", "Python"),
    ("print(", "Python"),
    ("public class", "Java"),
    ("console.log", "JavaScript"),
    ("#include", "C++"),
    ("func ", "Go"),
    ("fn ", "Rust"),
]


def _heuristic_language(code: str) -> str:
    """Fast heuristic when LLM is unavailable."""
    for pattern, lang in _FALLBACK_MAP:
        if pattern in code:
            return lang
    return "unknown"


def classifier_node(state: AgentState) -> AgentState:
    code = state.get("raw_code", "")
    requested = state.get("requested_language", "auto")

    nodes_executed = list(state.get("nodes_executed", []))
    nodes_executed.append("classifier")

    if not code.strip():
        return {
            **state,
            "input_type": "invalid",
            "detected_language": "unknown",
            "classifier_confidence": 0.0,
            "nodes_executed": nodes_executed,
        }

    # If user explicitly set a language (not "auto"), trust them
    if requested.lower() != "auto":
        logger.info(f"Language override: {requested}")
        return {
            **state,
            "detected_language": requested,
            "input_type": "code",
            "classifier_confidence": 1.0,
            "nodes_executed": nodes_executed,
        }

    try:
        result = safe_invoke(CLASSIFIER_PROMPT.format(code=code[:3000]))
        parsed = parse_json_response(result.content)
        lang = parsed.get("detected_language", "unknown")
        itype = parsed.get("input_type", "code")
        conf = float(parsed.get("confidence", 0.8))

        logger.info(f"Classifier: lang={lang} type={itype} conf={conf}")
        return {
            **state,
            "detected_language": lang,
            "input_type": itype,
            "classifier_confidence": conf,
            "provider_used": result.provider_used,
            "fallback_used": result.fallback_used,
            "nodes_executed": nodes_executed,
        }
    except Exception as exc:
        logger.warning(f"Classifier LLM failed, using heuristic: {exc}")
        return {
            **state,
            "detected_language": _heuristic_language(code),
            "input_type": "code",
            "classifier_confidence": 0.5,
            "nodes_executed": nodes_executed,
        }
