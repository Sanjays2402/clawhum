from __future__ import annotations
import time
import uuid
from collections import defaultdict, deque
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = rid
        resp = await call_next(request)
        resp.headers["x-request-id"] = rid
        return resp


class SimpleRateLimit(BaseHTTPMiddleware):
    """Per-IP token bucket; in-process only. Replace with Redis for HA."""

    def __init__(self, app, max_per_minute: int = 60):
        super().__init__(app)
        self.max = max_per_minute
        self.window = 60.0
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request, call_next):
        if request.url.path in {"/health", "/ready"}:
            return await call_next(request)
        ip = request.client.host if request.client else "0.0.0.0"
        now = time.monotonic()
        dq = self._hits[ip]
        while dq and now - dq[0] > self.window:
            dq.popleft()
        if len(dq) >= self.max:
            return JSONResponse({"detail": "rate limit"}, status_code=429)
        dq.append(now)
        return await call_next(request)
