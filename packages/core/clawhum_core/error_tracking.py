"""Sentry error tracking initialization.

Wires the official Sentry Python SDK with FastAPI integration when
``CLAWHUM_SENTRY_DSN`` is set. Designed to be a safe no-op in dev and
in CI: if the env var is empty or the SDK is not installed the helper
returns without raising.

What this gives operators:
- Real exception capture with stack traces from the FastAPI app.
- Release and environment tagging so issues group cleanly per deploy.
- A scrubber that strips the ``x-api-key`` header before send so API
  keys never reach Sentry's servers.
- A ``before_send`` hook that attaches the ClawHum request id (set by
  ``RequestIDMiddleware``) when present, making logs and Sentry events
  correlatable.

Configuration is env-driven and validated through ``Settings``. See
``Operations`` in ``README.md`` for tuning.
"""

from __future__ import annotations

from typing import Any

from .settings import get_settings
from .version import __version__

_SCRUB_HEADERS = {"x-api-key", "authorization", "cookie", "set-cookie"}


def _scrub(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Remove sensitive headers and attach request id if available."""
    try:
        req = event.get("request") or {}
        headers = req.get("headers") or {}
        if isinstance(headers, dict):
            for k in list(headers.keys()):
                if k.lower() in _SCRUB_HEADERS:
                    headers[k] = "[Filtered]"
            req["headers"] = headers
            event["request"] = req
        tags = event.setdefault("tags", {})
        if "request_id" not in tags:
            scope = (hint or {}).get("asgi_scope") or {}
            state = scope.get("state") or {}
            rid = state.get("request_id") if isinstance(state, dict) else None
            if rid:
                tags["request_id"] = rid
    except Exception:
        return event
    return event


def init_error_tracking() -> bool:
    """Initialize Sentry if configured. Returns True when active."""
    s = get_settings()
    dsn = (s.sentry_dsn or "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except Exception:
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=s.sentry_environment or "production",
        release=f"clawhum@{__version__}",
        traces_sample_rate=float(s.sentry_traces_sample_rate),
        profiles_sample_rate=float(s.sentry_profiles_sample_rate),
        send_default_pii=False,
        before_send=_scrub,
        integrations=[StarletteIntegration(), FastApiIntegration()],
    )
    return True
