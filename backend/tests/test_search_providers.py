import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_phase1.db")

import httpx

from app.config import settings
from app.services.search_providers import (SerperSearchProvider,
                                           TavilySearchProvider,
                                           provider_configuration)


class SearchProviderTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original = {name: getattr(settings, name) for name in ("serper_api_key", "tavily_api_key", "search_provider_order")}

    async def asyncTearDown(self):
        for name, value in self.original.items(): setattr(settings, name, value)

    async def test_serper_normalizes_results_and_freshness(self):
        settings.serper_api_key = "test-key"
        response = httpx.Response(200, request=httpx.Request("POST", "https://google.serper.dev/search"), json={"organic": [{"title": "Backend Engineer", "link": "https://example.com/jobs/1", "snippet": "FastAPI", "date": "1 day ago"}]})
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response)) as request:
            rows = await SerperSearchProvider().search("backend jobs", 5, "24h")
        self.assertEqual(rows[0]["source"], "serper")
        self.assertEqual(rows[0]["url"], "https://example.com/jobs/1")
        self.assertEqual(request.await_args.kwargs["json"]["tbs"], "qdr:d")

    async def test_tavily_normalizes_results_and_days(self):
        settings.tavily_api_key = "test-key"
        response = httpx.Response(200, request=httpx.Request("POST", "https://api.tavily.com/search"), json={"results": [{"title": "AI Engineer", "url": "https://example.com/jobs/2", "content": "RAG role", "published_date": "2026-09-03"}]})
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response)) as request:
            rows = await TavilySearchProvider().search("AI jobs", 5, "48h")
        self.assertEqual(rows[0]["source"], "tavily")
        self.assertEqual(rows[0]["snippet"], "RAG role")
        self.assertEqual(request.await_args.kwargs["json"]["days"], 2)

    async def test_configuration_never_exposes_keys(self):
        settings.serper_api_key = "do-not-expose"
        settings.tavily_api_key = ""
        settings.search_provider_order = "serper,tavily,ddg,searxng"
        payload = provider_configuration()
        self.assertEqual([row["provider"] for row in payload], ["serper", "tavily", "ddg", "searxng"])
        self.assertNotIn("do-not-expose", str(payload))


if __name__ == "__main__":
    unittest.main()
