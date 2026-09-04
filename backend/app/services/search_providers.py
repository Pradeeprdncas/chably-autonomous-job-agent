"""Pluggable web-search providers with ordered failover and safe telemetry."""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from ..config import settings
from ..data.mock_discovery import mock_results


def _freshness_code(freshness: str | None) -> str | None:
    value = (freshness or "").lower().strip()
    return {"24h": "d", "48h": "d", "day": "d", "week": "w", "7d": "w", "month": "m", "year": "y"}.get(value)


def _normalize(items, provider: str, limit: int) -> list[dict]:
    rows = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("link") or item.get("href") or "").strip()
        if not url:
            continue
        rows.append({
            "title": str(item.get("title") or item.get("name") or "").strip(),
            "url": url,
            "snippet": str(item.get("snippet") or item.get("content") or item.get("body") or "").strip(),
            "published_at": item.get("published_at") or item.get("published_date") or item.get("date"),
            "engine": str(item.get("engine") or provider),
            "source": provider,
        })
        if len(rows) >= limit:
            break
    return rows


class SearchProvider(ABC):
    name = "unknown"

    @property
    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    async def health_check(self) -> bool: ...

    @abstractmethod
    async def search(self, query: str, limit: int = 10, freshness: str | None = None) -> list[dict]: ...


class SerperSearchProvider(SearchProvider):
    name = "serper"

    @property
    def configured(self) -> bool: return bool(settings.serper_api_key)

    async def health_check(self) -> bool:
        if not self.configured:
            return False
        try:
            return bool(await self.search("chably search health check", 1))
        except Exception:
            return False

    async def search(self, query: str, limit: int = 10, freshness: str | None = None) -> list[dict]:
        if not self.configured: raise RuntimeError("SEARCH_PROVIDER_NOT_CONFIGURED")
        payload = {"q": query, "num": limit}
        if code := _freshness_code(freshness): payload["tbs"] = f"qdr:{code}"
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            response = await client.post("https://google.serper.dev/search", headers={"X-API-KEY": settings.serper_api_key, "Content-Type": "application/json"}, json=payload)
            response.raise_for_status()
        return _normalize(response.json().get("organic"), self.name, limit)


class TavilySearchProvider(SearchProvider):
    name = "tavily"

    @property
    def configured(self) -> bool: return bool(settings.tavily_api_key)

    async def health_check(self) -> bool:
        if not self.configured:
            return False
        try:
            return bool(await self.search("chably search health check", 1))
        except Exception:
            return False

    async def search(self, query: str, limit: int = 10, freshness: str | None = None) -> list[dict]:
        if not self.configured: raise RuntimeError("SEARCH_PROVIDER_NOT_CONFIGURED")
        payload = {"query": query, "max_results": limit, "search_depth": "basic", "include_answer": False, "include_raw_content": False}
        if freshness in {"24h", "48h", "day"}: payload["days"] = 1 if freshness != "48h" else 2
        elif freshness in {"7d", "week"}: payload["days"] = 7
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            response = await client.post("https://api.tavily.com/search", headers={"Authorization": f"Bearer {settings.tavily_api_key}", "Content-Type": "application/json"}, json=payload)
            response.raise_for_status()
        return _normalize(response.json().get("results"), self.name, limit)


class DDGSearchProvider(SearchProvider):
    name = "ddg"

    @property
    def configured(self) -> bool: return True

    async def health_check(self) -> bool:
        try: return bool(await self.search("chably search health check", 1))
        except Exception: return False

    async def _package_search(self, query: str, limit: int, freshness: str | None) -> list[dict]:
        from ddgs import DDGS
        values = await asyncio.to_thread(DDGS(timeout=settings.http_timeout_seconds).text, query, max_results=limit, timelimit=_freshness_code(freshness), backend="auto")
        return _normalize(values, self.name, limit)

    async def _html_search(self, query: str, limit: int) -> list[dict]:
        from bs4 import BeautifulSoup
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ChablyCareerSearch/1.0)"}
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, follow_redirects=True, headers=headers) as client:
            response = await client.get("https://html.duckduckgo.com/html/", params={"q": query})
            response.raise_for_status()
        soup, values = BeautifulSoup(response.text, "html.parser"), []
        for link in soup.select("a.result__a")[:limit]:
            url, parsed = link.get("href") or "", urlparse(link.get("href") or "")
            if "duckduckgo.com" in parsed.netloc: url = unquote((parse_qs(parsed.query).get("uddg") or [""])[0]) or url
            container = link.find_parent(class_="result")
            snippet = container.select_one(".result__snippet").get_text(" ", strip=True) if container and container.select_one(".result__snippet") else ""
            values.append({"title": link.get_text(" ", strip=True), "url": url, "snippet": snippet})
        return _normalize(values, self.name, limit)

    async def search(self, query: str, limit: int = 10, freshness: str | None = None) -> list[dict]:
        try: return await self._package_search(query, limit, freshness)
        except Exception: return await self._html_search(query, limit)


class SearXNGSearchProvider(SearchProvider):
    name = "searxng"

    @property
    def configured(self) -> bool: return bool(settings.searxng_url)

    async def health_check(self) -> bool:
        if not self.configured: return False
        try: return bool(await self.search("chably search health check", 1))
        except Exception: return False

    async def search(self, query: str, limit: int = 10, freshness: str | None = None) -> list[dict]:
        if not self.configured: raise RuntimeError("SEARCH_PROVIDER_NOT_CONFIGURED")
        params = {"q": query, "format": "json"}
        if code := _freshness_code(freshness): params["time_range"] = {"d": "day", "w": "week", "m": "month", "y": "year"}[code]
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            response = await client.get(f"{settings.searxng_url.rstrip('/')}/search", params=params)
            response.raise_for_status()
        payload = response.json()
        if not isinstance(payload.get("results"), list): raise RuntimeError("SEARCH_PROVIDER_INVALID_RESPONSE")
        values = [{**row, "snippet": row.get("content"), "engine": ",".join(row.get("engines") or [])} for row in payload["results"] if isinstance(row, dict)]
        return _normalize(values, self.name, limit)


class MockSearchProvider(SearchProvider):
    name = "mock"
    @property
    def configured(self) -> bool: return settings.search_mock_mode
    async def health_check(self) -> bool: return self.configured
    async def search(self, query: str, limit: int = 10, freshness: str | None = None) -> list[dict]: return mock_results()[:limit]


PROVIDERS = {"serper": SerperSearchProvider, "tavily": TavilySearchProvider, "ddg": DDGSearchProvider, "duckduckgo": DDGSearchProvider, "searxng": SearXNGSearchProvider}


class FailoverSearchProvider(SearchProvider):
    name = "failover"
    def __init__(self, providers: list[SearchProvider]): self.providers, self.last_attempts, self.last_provider = providers, [], None
    @property
    def configured(self) -> bool: return any(provider.configured for provider in self.providers)
    async def health_check(self) -> bool:
        for provider in self.providers:
            if provider.configured and await provider.health_check():
                return True
        return False
    async def search(self, query: str, limit: int = 10, freshness: str | None = None) -> list[dict]:
        self.last_attempts, self.last_provider = [], None
        for provider in self.providers:
            if not provider.configured:
                self.last_attempts.append({"provider": provider.name, "status": "not_configured", "latency_ms": 0, "result_count": 0, "error": None})
                continue
            started = time.perf_counter()
            try:
                rows = await provider.search(query, limit, freshness)
                status = "success" if rows else "empty"
                self.last_attempts.append({"provider": provider.name, "status": status, "latency_ms": round((time.perf_counter() - started) * 1000), "result_count": len(rows), "error": None})
                if rows: self.last_provider = provider.name; return rows
            except Exception as exc:
                self.last_attempts.append({"provider": provider.name, "status": "error", "latency_ms": round((time.perf_counter() - started) * 1000), "result_count": 0, "error": type(exc).__name__})
        return []


def get_search_provider() -> SearchProvider:
    if settings.search_mock_mode: return MockSearchProvider()
    order = [value.strip().lower() for value in settings.search_provider_order.split(",") if value.strip()]
    return FailoverSearchProvider([PROVIDERS[name]() for name in order if name in PROVIDERS])


def provider_configuration() -> list[dict]:
    providers = []
    for name in dict.fromkeys(settings.search_provider_order_list):
        provider_class = PROVIDERS.get(name)
        if provider_class:
            providers.append({"provider": name, "configured": provider_class().configured})
    return providers


# Backward-compatible names used by existing imports and tests.
SearXNGProvider = SearXNGSearchProvider
DuckDuckGoProvider = DDGSearchProvider
