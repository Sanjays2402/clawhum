from __future__ import annotations

import re
import secrets
import time
import uuid
from collections import defaultdict, deque

import structlog
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .api_keys import get_registry

# W3C Trace Context: version-traceid-parentid-flags
# https://www.w3.org/TR/trace-context/
_TRACEPARENT_RE = re.compile(
    r"^(?P<version>[0-9a-f]{2})-"
    r"(?P<trace_id>[0-9a-f]{32})-"
    r"(?P<span_id>[0-9a-f]{16})-"
    r"(?P<flags>[0-9a-f]{2})$"
)


def _parse_traceparent(value: str | None) -> tuple[str, str, str] | None:
    """Return (trace_id, parent_span_id, flags) if value is a valid W3C
    traceparent header with non-zero ids. Otherwise None.
    """
    if not value:
        return None
    m = _TRACEPARENT_RE.match(value.strip())
    if not m:
        return None
    trace_id = m.group("trace_id")
    span_id = m.group("span_id")
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None
    if m.group("version") == "ff":  # reserved invalid version
        return None
    return trace_id, span_id, m.group("flags")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a request id and W3C trace context to every request.

    - Honors inbound `X-Request-ID` and `traceparent` headers when present.
    - Generates a fresh trace id and span id otherwise (random, 128/64 bit).
    - Binds `request_id`, `trace_id`, `span_id`, `method`, and `path` into
      structlog contextvars so every log line emitted during the request
      carries them automatically.
    - Echoes `X-Request-ID` and `traceparent` on the response so callers
      can correlate logs across services.
    """

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        parsed = _parse_traceparent(request.headers.get("traceparent"))
        if parsed is not None:
            trace_id, parent_span_id, flags = parsed
        else:
            trace_id = secrets.token_hex(16)
            parent_span_id = ""
            flags = "01"
        # New span id for this hop, regardless of inbound parent.
        span_id = secrets.token_hex(8)
        traceparent_out = f"00-{trace_id}-{span_id}-{flags}"

        request.state.request_id = rid
        request.state.trace_id = trace_id
        request.state.span_id = span_id
        request.state.parent_span_id = parent_span_id
        request.state.traceparent = traceparent_out

        # Reset and bind per-request context so log lines downstream are
        # automatically correlated. clear_contextvars guards against
        # leakage between requests served by the same worker.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=rid,
            trace_id=trace_id,
            span_id=span_id,
            method=request.method,
            path=request.url.path,
        )
        try:
            resp = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        resp.headers["x-request-id"] = rid
        resp.headers["traceparent"] = traceparent_out
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
