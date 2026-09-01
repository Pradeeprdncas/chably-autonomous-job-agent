from __future__ import annotations

import asyncio
from sqlalchemy import text

from app.config import settings
from app.database import engine
from app.services.embedding_service import EmbeddingService
from app.services.job_discovery import SearXNGProvider


async def main():
    report = {"sqlite": "unavailable", "chroma": "unavailable", "gemini": "configured" if settings.gemini_api_key else "unconfigured", "mistral": "configured" if settings.mistral_api_key else "unconfigured", "searxng": "unconfigured", "google_oauth": "configured" if all([settings.google_client_id, settings.google_client_secret, settings.google_redirect_uri, settings.token_encryption_key]) else "unconfigured"}
    try:
        with engine.connect() as connection: connection.execute(text("SELECT 1"))
        report["sqlite"] = "online"
    except Exception: pass
    report["chroma"] = "online" if EmbeddingService().available else "unavailable"
    if settings.searxng_url: report["searxng"] = "online" if await SearXNGProvider().health_check() else "unavailable"
    print(report); return report


if __name__ == "__main__": asyncio.run(main())
