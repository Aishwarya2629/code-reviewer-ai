"""
Domain exceptions — each carries the HTTP status it should surface as.
Routes catch these and convert them via the global exception handler in main.py.
"""
from fastapi import HTTPException


class CodeReviewerError(HTTPException):
    """Base for all application exceptions."""


class InvalidInputError(CodeReviewerError):
    """Caller sent bad / unprocessable input → 422."""
    def __init__(self, detail: str):
        super().__init__(status_code=422, detail=detail)


class UnsupportedLanguageError(CodeReviewerError):
    """Requested language is not supported → 400."""
    def __init__(self, language: str):
        super().__init__(
            status_code=400,
            detail=f"Language '{language}' is not supported. "
                   f"Supported: Python, Java, JavaScript, TypeScript, C++, Go, Rust.",
        )


class LLMUnavailableError(CodeReviewerError):
    """All LLM providers failed → 503."""
    def __init__(self):
        super().__init__(
            status_code=503,
            detail="All AI model providers are currently unavailable. Please retry shortly.",
        )


class OCRFailureError(CodeReviewerError):
    """Image text extraction failed → 422."""
    def __init__(self, reason: str = "Could not extract text from image"):
        super().__init__(status_code=422, detail=reason)


class InputTooLargeError(CodeReviewerError):
    """Input exceeds configured max length → 413."""
    def __init__(self, field: str, limit: int):
        super().__init__(
            status_code=413,
            detail=f"'{field}' exceeds the maximum allowed length of {limit:,} characters.",
        )
