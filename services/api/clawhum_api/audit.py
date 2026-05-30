"""Append-only audit log for mutating API actions.

Records who did what when, persisted to a JSONL file. Designed for
forensic review and compliance, not high-volume analytics. Rotate the
file externally (logrotate, cron) once it grows.

Captured for every non-GET, non-HEAD, non-OPTIONS request that reaches
the audit middleware. Read endpoints are skipped to keep the log
focused on state changes. Health and metrics paths are always skipped.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from clawhum_core.logging import get_logger
from clawhum_core.settings import get_settings
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

_SKIP_PATHS = {"/health", "/ready", "/metrics"}
_SKIP_METHODS = {"GET", "HEAD", "OPTIONS"}

_lock = threading.Lock()
_log = get_logger("clawhum.audit")


def _actor_id(api_key: str | None) -> str:
    """Return a stable, non-reversible id for an API key (or 'anonymous')."""
    if not api_key:
        return "anonymous"
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    return f"key:{digest[:16]}"


def write_event(event: dict[str, Any], path: Path | None = None) -> None:
    """Append a single audit event as a JSON line. Best-effort, never raises."""
    target = path or get_settings().audit_log_path
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, separators=(",", ":"), sort_keys=True)
        # Open in append+binary mode to keep writes atomic on POSIX for
        # lines under PIPE_BUF. Good enough for single-process uvicorn.
        with _lock, open(target, "ab") as f:
            f.write(line.encode("utf-8") + b"\n")
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning("audit_write_failed", error=str(exc))


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Capture mutating requests and persist them to the audit log."""

    def __init__(self, app, enabled: bool = True):
        super().__init__(app)
        self.enabled = enabled

    async def dispatch(self, request: Request, call_next):
        if not self.enabled or request.method in _SKIP_METHODS or request.url.path in _SKIP_PATHS:
            return await call_next(request)

        started = time.time()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            try:
                actor = _actor_id(request.headers.get("x-api-key"))
                roles = getattr(request.state, "api_key_roles", None)
                event = {
                    "ts": started,
                    "actor": actor,
                    "api_key_name": getattr(request.state, "api_key_name", None),
                    "roles": sorted(roles) if roles else [],
                    "method": request.method,
                    "path": request.url.path,
                    "status": status_code,
                    "request_id": getattr(request.state, "request_id", None),
                    "client_ip": request.client.host if request.client else None,
                    "user_agent": request.headers.get("user-agent"),
                    "duration_ms": round((time.time() - started) * 1000, 2),
                }
                # Honor a test override path if the app set one.
                override = os.environ.get("CLAWHUM_AUDIT_LOG_PATH")
                path = Path(override) if override else None
                write_event(event, path=path)
                _log.info("audit", **event)
            except Exception as exc:  # pragma: no cover - defensive
                _log.warning("audit_emit_failed", error=str(exc))
