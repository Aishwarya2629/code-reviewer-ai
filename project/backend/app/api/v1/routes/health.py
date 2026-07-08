from fastapi import APIRouter
from app.core.config import get_settings
from app.models.schemas import HealthResponse
from app.services.llm_service import available_providers
from app.services.ocr_service import TESSERACT_AVAILABLE

router = APIRouter()
settings = get_settings()


@router.get("/health", response_model=HealthResponse, summary="Service health check")
async def health():
    providers = available_providers()
    if TESSERACT_AVAILABLE:
        providers.append("ocr/tesseract")
    return HealthResponse(
        status="ok",
        version=settings.APP_VERSION,
        providers_available=providers,
        mock_mode=settings.MOCK_MODE,
    )
