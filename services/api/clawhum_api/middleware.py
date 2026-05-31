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

from .api_keys import ANON_TENANT_ID, get_registry
from . import quota_store

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
        self.day_window = 86400.0
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        # Per-workspace minute and day buckets, independent of per-key.
        self._ws_minute: dict[str, deque[float]] = defaultdict(deque)
        self._ws_day: dict[str, deque[float]] = defaultdict(deque)

    def _bucket_key(self, request: Request) -> tuple[str, int, str]:
        """Return (bucket id, requests-per-minute limit, tenant_id)."""
        api_key = request.headers.get("x-api-key", "")
        if api_key:
            registry = get_registry()
            entry = registry.lookup(api_key)
            if entry is not None:
                limit = entry.rpm if entry.rpm > 0 else self.default_max
                tenant = entry.tenant_id or ANON_TENANT_ID
                return f"key:{entry.name}", limit, tenant
        ip = request.client.host if request.client else "0.0.0.0"
        return f"ip:{ip}", self.default_max, ANON_TENANT_ID

    @staticmethod
    def _trim(dq: deque[float], now: float, window: float) -> None:
        while dq and now - dq[0] > window:
            dq.popleft()

    def _limit_response(
        self,
        *,
        limit: int,
        retry_after: int,
        reset_epoch: int,
        scope: str,
    ) -> JSONResponse:
        return JSONResponse(
            {"detail": f"rate limit ({scope})", "scope": scope},
            status_code=429,
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset_epoch),
                "X-RateLimit-Scope": scope,
            },
        )

    async def dispatch(self, request, call_next):
        if request.url.path in {"/health", "/ready", "/metrics"}:
            return await call_next(request)
        bucket, limit, tenant_id = self._bucket_key(request)
        now = time.monotonic()
        wall = time.time()

        # Per-key (or per-IP) minute bucket. Existing behaviour preserved.
        dq = self._hits[bucket]
        self._trim(dq, now, self.window)
        if len(dq) >= limit:
            retry_after = max(1, int(self.window - (now - dq[0])))
            return self._limit_response(
                limit=limit,
                retry_after=retry_after,
                reset_epoch=int(wall + retry_after),
                scope="key",
            )

        # Workspace-level enforcement. A workspace can mint many keys;
        # the plan caps aggregate traffic so a noisy customer cannot
        # blow past the contract by spreading load across keys.
        plan = quota_store.get_plan(tenant_id)
        ws_min = self._ws_minute[tenant_id]
        ws_day = self._ws_day[tenant_id]
        self._trim(ws_min, now, self.window)
        self._trim(ws_day, now, self.day_window)

        if plan.rpm_ceiling > 0 and len(ws_min) >= plan.rpm_ceiling:
            retry_after = max(1, int(self.window - (now - ws_min[0])))
            return self._limit_response(
                limit=plan.rpm_ceiling,
                retry_after=retry_after,
                reset_epoch=int(wall + retry_after),
                scope="workspace_minute",
            )
        if plan.daily_quota > 0 and len(ws_day) >= plan.daily_quota:
            retry_after = max(1, int(self.day_window - (now - ws_day[0])))
            return self._limit_response(
                limit=plan.daily_quota,
                retry_after=retry_after,
                reset_epoch=int(wall + retry_after),
                scope="workspace_day",
            )

        dq.append(now)
        ws_min.append(now)
        ws_day.append(now)
        resp = await call_next(request)
        # Expose the tightest binding limit so well-behaved clients can
        # back off before they hit 429. The workspace ceiling wins when
        # set, otherwise the per-key bucket is reported.
        effective_limit = limit
        effective_remaining = max(0, limit - len(dq))
        effective_reset = int(wall + max(1, int(self.window - (now - dq[0]))))
        if plan.rpm_ceiling > 0:
            ws_remaining = max(0, plan.rpm_ceiling - len(ws_min))
            if ws_remaining < effective_remaining:
                effective_limit = plan.rpm_ceiling
                effective_remaining = ws_remaining
                effective_reset = int(wall + max(1, int(self.window - (now - ws_min[0]))))
        resp.headers["X-RateLimit-Limit"] = str(effective_limit)
        resp.headers["X-RateLimit-Remaining"] = str(effective_remaining)
        resp.headers["X-RateLimit-Reset"] = str(effective_reset)
        if plan.daily_quota > 0:
            resp.headers["X-RateLimit-Limit-Day"] = str(plan.daily_quota)
            resp.headers["X-RateLimit-Remaining-Day"] = str(
                max(0, plan.daily_quota - len(ws_day))
            )
        resp.headers["X-RateLimit-Plan"] = plan.plan
        return resp


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Emit baseline HTTP security headers on every response.

    Headers added (when not already set by the application or a proxy):

    - Strict-Transport-Security on HTTPS requests only
    - X-Content-Type-Options: nosniff
    - X-Frame-Options: DENY
    - Referrer-Policy
    - Permissions-Policy
    - Content-Security-Policy (locked down for a JSON API)
    - Cross-Origin-Opener-Policy / Cross-Origin-Resource-Policy

    Configurable via Settings. Designed to be safe to enable in front of
    every route including /metrics and /health. We do not strip the
    Server header here since Starlette does not set one by default and
    operators typically handle that at the ingress layer.
    """

    def __init__(
        self,
        app,
        *,
        enabled: bool = True,
        hsts_max_age: int = 63072000,
        hsts_include_subdomains: bool = True,
        hsts_preload: bool = False,
        csp: str = "default-src 'none'; frame-ancestors 'none'",
        referrer_policy: str = "no-referrer",
        permissions_policy: str = "geolocation=(), microphone=(), camera=()",
    ) -> None:
        super().__init__(app)
        self.enabled = enabled
        self.hsts_max_age = max(0, int(hsts_max_age))
        self.hsts_include_subdomains = hsts_include_subdomains
        self.hsts_preload = hsts_preload
        self.csp = csp or ""
        self.referrer_policy = referrer_policy or ""
        self.permissions_policy = permissions_policy or ""

    def _is_https(self, request: Request) -> bool:
        if request.url.scheme == "https":
            return True
        xfp = request.headers.get("x-forwarded-proto", "")
        return xfp.split(",")[0].strip().lower() == "https"

    def _hsts_value(self) -> str:
        parts = [f"max-age={self.hsts_max_age}"]
        if self.hsts_include_subdomains:
            parts.append("includeSubDomains")
        if self.hsts_preload:
            parts.append("preload")
        return "; ".join(parts)

    async def dispatch(self, request: Request, call_next):
        resp = await call_next(request)
        if not self.enabled:
            return resp
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        if self.referrer_policy:
            h.setdefault("Referrer-Policy", self.referrer_policy)
        if self.permissions_policy:
            h.setdefault("Permissions-Policy", self.permissions_policy)
        if self.csp:
            h.setdefault("Content-Security-Policy", self.csp)
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        h.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if self.hsts_max_age > 0 and self._is_https(request):
            h.setdefault("Strict-Transport-Security", self._hsts_value())
        return resp
