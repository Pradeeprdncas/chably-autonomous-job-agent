from fastapi import APIRouter
from sqlalchemy import text

from ..config import settings
from ..database import engine
from ..services.embedding_service import EmbeddingService
from ..services.job_discovery import SearXNGProvider
from .utils import ok

router = APIRouter(tags=["system"])


@router.get("/api/v1/system/status", summary="Get backend dependency status")
async def system_status():
    database_status = "offline"
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        database_status = "online"
    except Exception:
        database_status = "offline"

    service = EmbeddingService()
    vector_status = "online" if service.available else "unavailable"

    provider = "mock" if settings.ai_mock_mode else "gemini"
    search_configured = bool(settings.searxng_url)
    if settings.search_mock_mode:
        search_status = "mock"
    elif search_configured:
        search_status = "online" if await SearXNGProvider().health_check() else "unavailable"
    else:
        search_status = "unconfigured"
    data = {
        "api": "online",
        "database": {
            "status": database_status,
            "provider": "sqlite",
        },
        "vector_database": {
            "status": vector_status,
            "provider": "chromadb",
            "path": settings.resolved_chroma_path,
        },
        "ai": {
            "provider": provider,
            "configured": bool(settings.gemini_api_key) or settings.ai_mock_mode,
            "mock_mode": settings.ai_mock_mode,
        },
        "mistral": {"configured": bool(settings.mistral_api_key)},
        "search": {
            "provider": "mock" if settings.search_mock_mode else settings.search_provider,
            "configured": search_configured,
            "status": search_status,
            "mock_mode": settings.search_mock_mode,
        },
        "google_oauth": {
            "configured": all([settings.google_client_id, settings.google_client_secret, settings.google_redirect_uri, settings.token_encryption_key]),
            "mock_mode": settings.google_oauth_mock_mode,
        },
        "gmail": {"mock_mode": settings.google_oauth_mock_mode},
        "authentication": {"configured": bool(settings.jwt_secret_key) or settings.app_env != "production", "access_token_minutes": settings.access_token_expire_minutes, "refresh_token_days": settings.refresh_token_expire_days},
    }
    return ok("System status loaded successfully", data)
