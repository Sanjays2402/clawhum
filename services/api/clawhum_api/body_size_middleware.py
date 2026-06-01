"""Per-workspace request body size enforcement middleware.

Mirrors the BudgetMiddleware shape: resolves the tenant from the API
key header directly so the cap is applied *before* the route runs,
without waiting on per-route auth dependencies. Rejects payloads that
exceed the workspace ``max_bytes`` policy with HTTP 413 and a
machine-readable JSON body so SDKs can chunk and retry cleanly.

Two enforcement points:

  * Pre-flight: if the client sent a ``Content-Length`` larger than
    the cap, reject immediately without touching the body stream.
  * Streaming: wrap ``receive`` so a chunked sender that lies about
    the length is still cut off the moment the running total crosses
    the cap. We send a 413 and stop forwarding bytes to the route.

The middleware is no-op for tenants with ``max_bytes == 0`` (default).
Health, metrics, and the body-size admin surface itself are always
skipped so an admin can recover from a self-inflicted lockout.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from . import body_size, pat_store
from .api_keys import ANON_TENANT_ID, get_registry

_SKIP_PREFIXES = (
    "/health",
    "/ready",
    "/metrics",
    "/body-size",
    "/v1/body-size",
)


def _skip(path: str) -> bool:
    if path in {"/health", "/ready", "/metrics"}:
        return True
    for p in _SKIP_PREFIXES:
        if path == p or path.startswith(p + "/"):
            return True
    return False


def _resolve_tenant(request) -> str:
    api_key = request.headers.get("x-api-key", "")
    if api_key:
        entry = get_registry().lookup(api_key)
        if entry is not None and entry.tenant_id:
            return entry.tenant_id
        if pat_store.looks_like_pat(api_key):
            pat = pat_store.lookup_by_secret(api_key)
            if pat is not None and pat.tenant_id:
                return pat.tenant_id
    return ANON_TENANT_ID


def _too_large(cap: int, observed: int | None) -> JSONResponse:
    body = {
        "code": "request_body_too_large",
        "message": (
            f"request body exceeds workspace cap of {cap} bytes"
        ),
        "max_bytes": cap,
    }
    if observed is not None:
        body["observed_bytes"] = observed
    return JSONResponse(
        status_code=413,
        content=body,
        headers={"X-Body-Size-Limit": str(cap)},
    )


class BodySizeMiddleware(BaseHTTPMiddleware):
    """Enforce the workspace max request body size on every route."""

    async def dispatch(self, request, call_next):
        if _skip(request.url.path) or request.method in ("GET", "HEAD", "OPTIONS", "DELETE"):
            return await call_next(request)

        tenant = _resolve_tenant(request)
        cap = body_size.max_bytes(tenant)
        if cap <= 0:
            return await call_next(request)

        # Pre-flight: if the client volunteered a Content-Length we can
        # reject before reading a byte off the wire.
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                declared = int(cl)
            except ValueError:
                declared = -1
            if declared > cap:
                return _too_large(cap, declared)

        # Streaming guard. Wrap the receive channel so a chunked sender
        # that lies about Content-Length is still bounded; the moment
        # the running total crosses the cap we abort with 413 and stop
        # forwarding bytes downstream.
        original_receive = request.receive
        total = 0
        exceeded = {"flag": False}

        async def _capped_receive():
            nonlocal total
            message = await original_receive()
            if message.get("type") == "http.request":
                body = message.get("body", b"") or b""
                total += len(body)
                if total > cap:
                    exceeded["flag"] = True
                    # Truncate and signal end of stream so downstream
                    # never sees a body larger than the cap.
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        request._receive = _capped_receive  # type: ignore[attr-defined]
        response = await call_next(request)
        if exceeded["flag"]:
            return _too_large(cap, total)
        return response
