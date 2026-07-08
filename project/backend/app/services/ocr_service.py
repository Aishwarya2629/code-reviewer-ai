"""
OCR service — cross-platform Tesseract wrapper with OpenCV preprocessing.

Cross-platform note: We auto-discover tesseract on PATH (Linux/macOS) and
fall back to common Windows install paths. No hardcoded user-home paths.
"""
from __future__ import annotations

import os
import re
import shutil
import traceback
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import pytesseract
from PIL import Image

from app.core.logging_config import get_logger

logger = get_logger(__name__)

# ── Tesseract discovery ───────────────────────────────────────────────────────

_WINDOWS_CANDIDATES = [
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
]


def _configure_tesseract() -> bool:
    """Return True if tesseract is usable."""
    # 1. Already on PATH?
    if shutil.which("tesseract"):
        return True

    # 2. Common Windows install paths
    for candidate in _WINDOWS_CANDIDATES:
        if candidate.exists():
            pytesseract.pytesseract.tesseract_cmd = str(candidate)
            logger.info(f"Tesseract found at {candidate}")
            return True

    logger.warning(
        "Tesseract not found. Image-to-code feature will be unavailable. "
        "Install with: sudo apt install tesseract-ocr   (Linux) "
        "or brew install tesseract   (macOS)"
    )
    return False


TESSERACT_AVAILABLE: bool = _configure_tesseract()


# ── Preprocessing pipeline ────────────────────────────────────────────────────

def _preprocess(image_path: str) -> np.ndarray:
    """
    Multi-step OpenCV pipeline to maximise OCR accuracy on code screenshots.

    Steps: upscale → greyscale → denoise → sharpen → adaptive threshold.
    Adaptive threshold outperforms simple binary for variable backgrounds.
    """
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"cv2 could not decode image at {image_path!r}")

    # 2× upscale improves small-font recognition significantly
    img = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Non-local means denoising — preserves edges better than Gaussian blur
    gray = cv2.fastNlMeansDenoising(gray, h=10, templateWindowSize=7, searchWindowSize=21)

    # Unsharp-mask sharpening kernel
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    gray = cv2.filter2D(gray, -1, kernel)

    # Adaptive threshold handles dark-mode / light-mode code editors equally
    binarised = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=31, C=2,
    )
    return binarised


# ── OCR cleanup ───────────────────────────────────────────────────────────────

_OCR_REPLACEMENTS = {
    "\u2018": "'", "\u2019": "'",   # smart single quotes
    "\u201c": '"', "\u201d": '"',   # smart double quotes
    "|": "I",                        # pipe misread as uppercase-I
    "\t": "    ",                    # tabs → 4 spaces
}


def _clean(text: str) -> str:
    for bad, good in _OCR_REPLACEMENTS.items():
        text = text.replace(bad, good)
    text = re.sub(r"[ ]{2,}", " ", text)       # collapse multiple spaces
    text = re.sub(r"\n{3,}", "\n\n", text)      # max 2 consecutive newlines
    text = re.sub(r"[^\x09\x0a\x0d\x20-\x7e]", " ", text)  # strip non-ASCII
    return text.strip()


# ── Public API ────────────────────────────────────────────────────────────────

def extract_text_from_image(image_path: str) -> str:
    """
    Extract text from a code screenshot.

    Returns empty string on failure — callers should treat that as
    OCRFailureError and return a 422 to the client.
    """
    if not TESSERACT_AVAILABLE:
        logger.error("extract_text_from_image called but Tesseract is not installed")
        return ""

    try:
        processed = _preprocess(image_path)
        # psm 6 = uniform block of text (best for code screenshots)
        custom_config = "--oem 3 --psm 6 -c preserve_interword_spaces=1"
        raw = pytesseract.image_to_string(processed, config=custom_config)
        cleaned = _clean(raw)
        logger.info(f"OCR extracted {len(cleaned)} characters from {image_path}")
        return cleaned

    except Exception:
        logger.error(f"OCR failed for {image_path}\n{traceback.format_exc()}")
        return ""


def classify_extracted_text(text: str) -> str:
    """
    Heuristically decide if extracted text is 'code' or 'problem'.

    Scoring: each matched pattern adds 1 point. Whichever bucket wins,
    we return. Ties go to 'problem' (safer fallback for the solver).
    """
    lower = text.lower()

    code_signals = [
        "def ", "class ", "return ", "#include", "public class",
        "console.log", "import ", "function ", "=>", "std::",
        "println", "system.out", "using namespace", "main(",
        "void ", "int main", "for(", "while(", "if(",
    ]
    problem_signals = [
        "given an", "given a", "constraints", "example", "examples",
        "input:", "output:", "leetcode", "find the", "return the",
        "problem statement", "explanation:", "note:", "you may assume",
    ]

    code_score = sum(1 for s in code_signals if s in lower)
    problem_score = sum(1 for s in problem_signals if s in lower)

    logger.info(f"classify_extracted_text code={code_score} problem={problem_score}")
    return "code" if code_score > problem_score else "problem"
