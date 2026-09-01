from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse

from .config import settings

logger = logging.getLogger("chably.requests")
REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_requests: dict[str, deque] = defaultdict(deque)
SENSITIVE = ("/auth/login", "/auth/register", "/resumes/upload", "/job-search", "/draft-email", "/send", "/sync")


async def request_context_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", "")
    if not REQUEST_ID.fullmatch(request_id):
        request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    started = time.perf_counter()
    key = f"{request.client.host if request.client else 'unknown'}:{request.url.path}"
    queue = _requests[key]; now = time.monotonic()
    while queue and queue[0] < now - settings.rate_limit_window_seconds:
        queue.popleft()
    limit = settings.rate_limit_sensitive if any(term in request.url.path for term in SENSITIVE) else settings.rate_limit_default
    if len(queue) >= limit:
        response = JSONResponse(status_code=429, content={"success": False, "message": "Rate limit exceeded", "data": None, "meta": {"request_id": request_id}, "errors": [{"code": "RATE_LIMIT_EXCEEDED", "field": None, "message": "Try again later."}]})
    else:
        queue.append(now)
        response = await call_next(request)
    duration = round((time.perf_counter() - started) * 1000)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    logger.info(json.dumps({"timestamp": time.time(), "level": "INFO", "request_id": request_id, "route": request.url.path, "method": request.method, "status": response.status_code, "duration_ms": duration}, separators=(",", ":")))
    return response
