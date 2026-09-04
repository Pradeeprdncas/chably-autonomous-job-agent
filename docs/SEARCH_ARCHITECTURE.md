# Search architecture

Chably uses an ordered provider chain rather than coupling job discovery to one service.

Default order:

```text
serper -> tavily -> ddg -> searxng
```

Set it with `SEARCH_PROVIDER_ORDER`. Unconfigured providers are skipped. Errors and empty responses fall through to the next provider. DDG requires no key; the `ddgs` package is preferred and the existing HTML implementation remains a runtime compatibility fallback. SearXNG is optional.

Provider attempts are persisted in each `JobSearchSession.progress` record with the provider, status, latency, result count, safe error class, query, and timestamp. API keys and response bodies are never logged.

Optional configuration:

```env
SEARCH_PROVIDER_ORDER=serper,tavily,ddg,searxng
SERPER_API_KEY=
TAVILY_API_KEY=
SEARXNG_URL=
```

Search requests accept an optional `freshness` value: `24h`, `48h`, `day`, `7d`, `week`, `month`, or `year`. A freshness request constrains the provider where supported; it does not fabricate a posting date. Search results retain `published_at` only when the provider supplies it.

Inspect non-secret provider health at `GET /api/v1/system/diagnostics`. The endpoint reports configuration and reachability but never returns credentials.
