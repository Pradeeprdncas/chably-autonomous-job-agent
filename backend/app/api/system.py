from fastapi import APIRouter
from sqlalchemy import text

from ..config import settings
from ..database import engine
from ..services.embedding_service import EmbeddingService
from ..services.search_providers import PROVIDERS, provider_configuration
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
    search_configured = any(item["configured"] for item in provider_configuration())
    if settings.search_mock_mode:
        search_status = "mock"
    elif search_configured:
        search_status = "configured"
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
            "provider": "mock" if settings.search_mock_mode else "failover",
            "order": settings.search_provider_order_list,
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


@router.get("/api/v1/system/diagnostics", summary="Get non-secret development diagnostics")
async def system_diagnostics():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        database_status = "online"
    except Exception:
        database_status = "unavailable"
    providers = []
    for item in provider_configuration():
        healthy = False
        if item["configured"]:
            try: healthy = await PROVIDERS[item["provider"]]().health_check()
            except Exception: healthy = False
        providers.append({**item, "status": "online" if healthy else "unavailable" if item["configured"] else "not_configured"})
    return ok("System diagnostics loaded", {
        "database": {"status": database_status},
        "ai_provider": {"gemini_configured": bool(settings.gemini_api_key), "mistral_configured": bool(settings.mistral_api_key), "mock_mode": settings.ai_mock_mode},
        "search_providers": {"order": settings.search_provider_order_list, "providers": providers, "mock_mode": settings.search_mock_mode},
        "google_oauth": {"configured": all([settings.google_client_id, settings.google_client_secret, settings.google_redirect_uri, settings.token_encryption_key])},
        "gmail": {"connection_scope": "per_authenticated_user", "mock_mode": settings.google_oauth_mock_mode},
        "job_adapters": {"greenhouse": True, "lever": True, "ashby": True},
        "crawler": {"static_html": True, "browser_enabled": settings.browser_fetch_enabled},
        "background_worker": {"configured": False},
    })
