from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .api_keys import get_registry


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = rid
        resp = await call_next(request)
        resp.headers["x-request-id"] = rid
        return resp


class SimpleRateLimit(BaseHTTPMiddleware):
    """Per-key (if X-API-Key present) or per-IP sliding window limiter.

    Each API key has an independent bucket sized by its configured rpm
    (or the default when unspecified). Requests without a known key fall
    back to a per-IP bucket sized by the default. In-process only;
    replace with Redis for multi-replica deployments.

    Sets X-RateLimit-Remaining and X-RateLimit-Limit response headers so
    clients can adapt. Returns 429 with a Retry-After header on overflow.
    """

    def __init__(self, app, max_per_minute: int = 120):
        super().__init__(app)
        self.default_max = max(1, int(max_per_minute))
        self.window = 60.0
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _bucket_key(self, request: Request) -> tuple[str, int]:
        """Return (bucket id, requests-per-minute limit) for this request."""
        api_key = request.headers.get("x-api-key", "")
        if api_key:
            registry = get_registry()
            entry = registry.lookup(api_key)
            if entry is not None:
                limit = entry.rpm if entry.rpm > 0 else self.default_max
                return f"key:{entry.name}", limit
        ip = request.client.host if request.client else "0.0.0.0"
        return f"ip:{ip}", self.default_max

    async def dispatch(self, request, call_next):
        if request.url.path in {"/health", "/ready", "/metrics"}:
            return await call_next(request)
        bucket, limit = self._bucket_key(request)
        now = time.monotonic()
        dq = self._hits[bucket]
        while dq and now - dq[0] > self.window:
            dq.popleft()
        if len(dq) >= limit:
            retry_after = max(1, int(self.window - (now - dq[0])))
            return JSONResponse(
                {"detail": "rate limit"},
                status_code=429,
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                },
            )
        dq.append(now)
        resp = await call_next(request)
        resp.headers["X-RateLimit-Limit"] = str(limit)
        resp.headers["X-RateLimit-Remaining"] = str(max(0, limit - len(dq)))
        return resp
