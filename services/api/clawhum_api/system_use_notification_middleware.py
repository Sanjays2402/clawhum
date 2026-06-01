"""Per-workspace system-use notification enforcement middleware.

Reject mutating requests with HTTP 403 ``system_use_ack_required``
when the workspace has an enforced banner and the calling actor has
not yet acknowledged the current revision.

The middleware resolves the actor identity from the API key header
directly (mirroring ``BodySizeMiddleware``) so the gate fires before
any route runs and no per-route dependency can forget to call it.
GETs and OPTIONS are always allowed so a client can fetch the banner
text and the workspace metadata it needs to render the consent
prompt. The ack endpoint itself is skipped so an acknowledgement is
always possible. Health, metrics, MFA, SSO, and auth surfaces are
skipped so a locked-out workspace can recover and so an unenrolled
caller is not blocked from enrolling MFA before they can ack.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from . import pat_store, system_use_notification as sun
from .api_keys import get_registry

_SKIP_PREFIXES = (
    "/health",
    "/ready",
    "/metrics",
    "/mfa",
    "/v1/mfa",
    "/sso",
    "/v1/sso",
    "/sessions",
    "/v1/sessions",
    "/me",
    "/v1/me",
    "/scim",
    "/v1/scim",
    "/system-use-notification",
    "/v1/system-use-notification",
)


def _skip_path(path: str) -> bool:
    if path in {"/", "/health", "/ready", "/metrics", "/openapi.json", "/docs", "/redoc"}:
        return True
    for p in _SKIP_PREFIXES:
        if path == p or path.startswith(p + "/"):
            return True
    return False


def _resolve_principal(request) -> tuple[str, str]:
    """Return (tenant_id, actor_id) resolved from the API key header.

    Returns ``("", "")`` when the credential is missing or unknown so
    the middleware does not synthesise a principal; the per-route
    auth dependency will reject the call with the correct 401 shape.
    """
    api_key = request.headers.get("x-api-key", "")
    if not api_key:
        return "", ""
    entry = get_registry().lookup(api_key)
    if entry is not None and entry.tenant_id:
        return entry.tenant_id, entry.name or ""
    if pat_store.looks_like_pat(api_key):
        pat = pat_store.lookup_by_secret(api_key)
        if pat is not None and pat.tenant_id:
            return pat.tenant_id, pat.id or f"pat:{pat.name}"
    return "", ""


def _denied(banner: sun.Banner) -> JSONResponse:
    body = {
        "code": "system_use_ack_required",
        "message": (
            "this workspace requires acknowledgement of the system use "
            "notification before mutating actions are permitted"
        ),
        "revision": banner.revision,
        "title": banner.title,
        "ack_endpoint": "/system-use-notification/ack",
    }
    return JSONResponse(
        status_code=403,
        content=body,
        headers={
            "X-System-Use-Ack-Required": "1",
            "X-System-Use-Notification-Revision": str(banner.revision),
        },
    )


class SystemUseNotificationMiddleware(BaseHTTPMiddleware):
    """Block mutating routes when the actor has not acked the banner."""

    async def dispatch(self, request, call_next):
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)
        if _skip_path(request.url.path):
            return await call_next(request)
        tenant, actor = _resolve_principal(request)
        if not tenant or not actor:
            return await call_next(request)
        banner = sun.needs_ack(tenant, actor)
        if banner is not None:
            return _denied(banner)
        return await call_next(request)
