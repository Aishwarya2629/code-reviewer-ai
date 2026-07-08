"""
/api/v1/problem — DSA problem solver endpoint.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.config import get_settings
from app.core.exceptions import InputTooLargeError
from app.core.logging_config import get_logger
from app.models.schemas import (
    ProblemRequest, ProblemResponse,
    DSASolution, ComplexityInfo,
)
from app.prompts.templates import PROBLEM_PROMPT
from app.services.llm_service import safe_invoke, parse_json_response

router = APIRouter()
logger = get_logger(__name__)
settings = get_settings()


def _mock_solutions(language: str) -> list:
    template = "# {title} solution in {language}\npass"
    return [
        {
            "title": t,
            "approach": f"{t} approach",
            "clean_code": template.format(title=t, language=language),
            "commented_code": f"# {t}\n" + template.format(title=t, language=language),
            "time_complexity": {"time": "O(?)", "space": "O(?)", "reasoning": "Mock"},
            "space_complexity": {"time": "O(?)", "space": "O(?)", "reasoning": "Mock"},
        }
        for t in ("Brute Force", "Better", "Optimised", "Advanced")
    ]


@router.post(
    "/problem",
    response_model=ProblemResponse,
    summary="Solve a DSA problem with 4 progressive approaches",
)
async def solve_problem(payload: ProblemRequest, request: Request):
    request_id = str(request.state.request_id)

    if len(payload.problem) > settings.MAX_PROBLEM_LENGTH:
        raise InputTooLargeError("problem", settings.MAX_PROBLEM_LENGTH)

    try:
        prompt = PROBLEM_PROMPT.format(
            language=payload.language.value,
            problem=payload.problem,
        )
        result = safe_invoke(prompt, lambda: {"solutions": _mock_solutions(payload.language.value)})
        parsed = parse_json_response(result.content)
        raw_solutions = parsed.get("solutions", [])

    except Exception as exc:
        logger.error(f"Problem solver LLM error: {exc}")
        raw_solutions = _mock_solutions(payload.language.value)
        result = type("R", (), {"provider_used": "mock", "fallback_used": True})()

    # Normalise and pad to 4 solutions
    solutions = []
    for s in raw_solutions[:4]:
        t = s.get("time_complexity", {})
        sp = s.get("space_complexity", {})
        if isinstance(t, str):
            t = {"time": t, "space": "O(?)", "reasoning": ""}
        if isinstance(sp, str):
            sp = {"time": "O(?)", "space": sp, "reasoning": ""}
        solutions.append(DSASolution(
            title=s.get("title", "Solution"),
            approach=s.get("approach", ""),
            clean_code=s.get("clean_code", ""),
            commented_code=s.get("commented_code", s.get("clean_code", "")),
            time_complexity=ComplexityInfo(
                time=t.get("time", "O(?)"),
                space=t.get("space", "O(?)"),
                reasoning=t.get("reasoning", ""),
            ),
            space_complexity=ComplexityInfo(
                time=sp.get("time", "O(?)"),
                space=sp.get("space", "O(?)"),
                reasoning=sp.get("reasoning", ""),
            ),
        ))

    # Pad if fewer than 4 returned
    titles = ["Brute Force", "Better", "Optimised", "Advanced"]
    while len(solutions) < 4:
        idx = len(solutions)
        solutions.append(DSASolution(
            title=titles[idx],
            approach="Unavailable",
            clean_code="# Solution unavailable",
            commented_code="# Solution unavailable",
            time_complexity=ComplexityInfo(time="N/A", space="N/A", reasoning=""),
            space_complexity=ComplexityInfo(time="N/A", space="N/A", reasoning=""),
        ))

    return ProblemResponse(
        request_id=request_id,
        valid=True,
        solutions=solutions,
        fallback_used=getattr(result, "fallback_used", False),
        provider_used=getattr(result, "provider_used", None),
    )
