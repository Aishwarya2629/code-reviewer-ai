"""
LangGraph pipeline for code review.

Node execution order:
  classifier → security_scanner → complexity_analyzer → optimizer
                                                              ↓
                                           [if invalid] → END (short-circuit)
                                           [if valid]   → validator → explainer → END

Design decisions worth discussing in an interview:
- Conditional edges allow early exit for invalid inputs without running
  expensive LLM calls (optimizer, validator, explainer cost real $).
- Each node is a pure function: (AgentState) → AgentState.
  This makes them independently testable and replaceable.
- The validator provides a rollback safety net — if the optimizer produces
  broken code, we revert and still deliver a useful response.
"""
from __future__ import annotations

import time
from typing import Literal

from langgraph.graph import StateGraph, END

from app.agents.state import AgentState
from app.agents.nodes.classifier import classifier_node
from app.agents.nodes.security_scanner import security_scanner_node
from app.agents.nodes.complexity_analyzer import complexity_analyzer_node
from app.agents.nodes.optimizer import optimizer_node
from app.agents.nodes.validator import validator_node
from app.agents.nodes.explainer import explainer_node
from app.core.logging_config import get_logger

logger = get_logger(__name__)


# ── Routing functions (conditional edges) ────────────────────────────────────

def route_after_classifier(state: AgentState) -> Literal["security_scanner", "end_invalid"]:
    """Short-circuit on invalid input to avoid wasting LLM calls."""
    if state.get("input_type") == "invalid":
        return "end_invalid"
    return "security_scanner"


def route_after_optimizer(state: AgentState) -> Literal["validator", "end_invalid"]:
    """If optimizer marked the code as invalid, end early."""
    if state.get("input_type") == "invalid":
        return "end_invalid"
    return "validator"


def end_invalid_node(state: AgentState) -> AgentState:
    """Terminal node for invalid inputs — adds a clean error message."""
    return {
        **state,
        "analysis": "Input could not be processed.",
        "explanation": state.get(
            "pipeline_error",
            "The submitted code is invalid, empty, or could not be parsed."
        ),
        "optimized_code": state.get("raw_code", ""),
        "after_time": "N/A",
        "after_space": "N/A",
        "changes_made": [],
        "security_findings": state.get("security_findings", []),
    }


# ── Graph construction ────────────────────────────────────────────────────────

def build_review_graph() -> StateGraph:
    builder = StateGraph(AgentState)

    builder.add_node("classifier", classifier_node)
    builder.add_node("security_scanner", security_scanner_node)
    builder.add_node("complexity_analyzer", complexity_analyzer_node)
    builder.add_node("optimizer", optimizer_node)
    builder.add_node("validator", validator_node)
    builder.add_node("explainer", explainer_node)
    builder.add_node("end_invalid", end_invalid_node)

    builder.set_entry_point("classifier")

    builder.add_conditional_edges(
        "classifier",
        route_after_classifier,
        {"security_scanner": "security_scanner", "end_invalid": "end_invalid"},
    )

    builder.add_edge("security_scanner", "complexity_analyzer")
    builder.add_edge("complexity_analyzer", "optimizer")

    builder.add_conditional_edges(
        "optimizer",
        route_after_optimizer,
        {"validator": "validator", "end_invalid": "end_invalid"},
    )

    builder.add_edge("validator", "explainer")
    builder.add_edge("explainer", END)
    builder.add_edge("end_invalid", END)

    return builder.compile()


# Singleton — compiled once at module load
review_graph = build_review_graph()


def run_review_pipeline(
    code: str,
    language: str = "auto",
    request_id: str = "-",
) -> AgentState:
    """
    Entry point called by the route handler.
    Returns the final AgentState after the full pipeline.
    """
    t0 = time.perf_counter()
    initial_state: AgentState = {
        "raw_code": code,
        "requested_language": language,
        "request_id": request_id,
        "nodes_executed": [],
        "security_findings": [],
        "changes_made": [],
        "fallback_used": False,
        "provider_used": "",
    }

    final_state: AgentState = review_graph.invoke(initial_state)

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        f"Pipeline complete request_id={request_id} "
        f"nodes={final_state.get('nodes_executed')} "
        f"elapsed_ms={elapsed_ms}"
    )
    final_state["pipeline_ms"] = elapsed_ms
    return final_state
