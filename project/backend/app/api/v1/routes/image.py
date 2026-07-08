"""
/api/v1/image — Upload an image, extract code/problem via OCR, run pipeline.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile

from app.agents.graph import run_review_pipeline
from app.api.v1.routes.problem import solve_problem
from app.core.config import get_settings
from app.core.exceptions import OCRFailureError, InvalidInputError
from app.core.logging_config import get_logger
from app.models.schemas import (
    ImageReviewResponse, ProblemRequest,
    ReviewRequest, SupportedLanguage, ReviewResponse,
    ComplexityInfo, SecurityIssue, OptimizationChange,
)
from app.services.ocr_service import (
    extract_text_from_image, classify_extracted_text, TESSERACT_AVAILABLE,
)

router = APIRouter()
logger = get_logger(__name__)
settings = get_settings()

_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_TEMP_DIR = Path("temp")


@router.post(
    "/image",
    response_model=ImageReviewResponse,
    summary="Upload a code screenshot or problem image",
)
async def image_to_review(
    request: Request,
    file: UploadFile = File(...),
    language: str = Form("auto"),
):
    request_id = str(request.state.request_id)

    if not TESSERACT_AVAILABLE:
        raise InvalidInputError(
            "Tesseract OCR is not installed on this server. "
            "Image uploads are unavailable. Please paste code as text instead."
        )

    # ── Validate file ─────────────────────────────────────────────────────────
    ext = Path(file.filename or "upload.png").suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise InvalidInputError(
            f"Unsupported file type '{ext}'. Upload PNG, JPG, JPEG, or WEBP."
        )

    contents = await file.read()
    if not contents:
        raise InvalidInputError("Uploaded file is empty.")

    size_mb = len(contents) / (1024 * 1024)
    if size_mb > settings.MAX_IMAGE_SIZE_MB:
        raise InvalidInputError(
            f"Image is {size_mb:.1f} MB; maximum allowed is {settings.MAX_IMAGE_SIZE_MB} MB."
        )

    # ── Save, OCR, clean up ───────────────────────────────────────────────────
    _TEMP_DIR.mkdir(exist_ok=True)
    tmp_path = _TEMP_DIR / f"{uuid.uuid4()}{ext}"

    try:
        tmp_path.write_bytes(contents)
        extracted = extract_text_from_image(str(tmp_path))
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

    if not extracted.strip():
        raise OCRFailureError("Could not extract any text from the image. "
                              "Ensure the image is clear and contains visible code.")

    # ── Route to review or problem solver ─────────────────────────────────────
    detected_type = classify_extracted_text(extracted)
    logger.info(f"Image classified as: {detected_type}")

    # Resolve language enum safely
    try:
        lang_enum = SupportedLanguage(language)
    except ValueError:
        lang_enum = SupportedLanguage.AUTO

    if detected_type == "code":
        state = run_review_pipeline(
            code=extracted, language=lang_enum.value, request_id=request_id
        )
        security_issues = [
            SecurityIssue(**{k: f[k] for k in SecurityIssue.model_fields})
            for f in state.get("security_findings", [])
            if all(k in f for k in SecurityIssue.model_fields)
        ]
        review = ReviewResponse(
            request_id=request_id,
            valid=True,
            already_optimal=state.get("already_optimal", False),
            detected_language=state.get("detected_language", "unknown"),
            original_code=extracted,
            optimized_code=state.get("optimized_code", extracted),
            before_complexity=ComplexityInfo(
                time=state.get("before_time", "O(?)"),
                space=state.get("before_space", "O(?)"),
                reasoning=state.get("complexity_reasoning", ""),
            ),
            after_complexity=ComplexityInfo(
                time=state.get("after_time", "O(?)"),
                space=state.get("after_space", "O(?)"),
                reasoning="",
            ),
            security_issues=security_issues,
            changes_made=[
                OptimizationChange(**c) for c in state.get("changes_made", [])
                if all(k in c for k in OptimizationChange.model_fields)
            ],
            explanation=state.get("explanation", ""),
            analysis=state.get("analysis", ""),
            fallback_used=state.get("fallback_used", False),
            provider_used=state.get("provider_used"),
            pipeline_ms=state.get("pipeline_ms"),
        )
        return ImageReviewResponse(
            request_id=request_id, valid=True,
            detected_type="code", extracted_text=extracted, review=review,
        )

    else:
        # Problem statement
        problem_resp = await solve_problem(
            ProblemRequest(problem=extracted, language=lang_enum), request
        )
        return ImageReviewResponse(
            request_id=request_id, valid=True,
            detected_type="problem", extracted_text=extracted,
            problem_solutions=problem_resp,
        )
