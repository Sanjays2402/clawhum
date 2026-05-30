"""GDPR data lifecycle endpoints.

The caller is identified by their API key. Both endpoints operate only
on data attributable to that key's actor digest. Anonymous (no key)
callers may export and erase the shared "anonymous" bucket, which is
expected in single tenant dev mode.

Endpoints:
    GET    /v1/privacy/export   returns audit events for the caller
    DELETE /v1/privacy/me       redacts actor fields in matching audit
                                 events and returns a count
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from clawhum_core.settings import get_settings
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from ..auth import require_api_key
from ..privacy import actor_id_for, collect_events, redact_actor

router = APIRouter(prefix="/v1/privacy", tags=["privacy"], dependencies=[Depends(require_api_key)])


def _audit_path() -> Path:
    override = os.environ.get("CLAWHUM_AUDIT_LOG_PATH")
    if override:
        return Path(override)
    return get_settings().audit_log_path


@router.get("/export")
async def export_my_data(
    request: Request,
    x_api_key: str = Header(default=""),
) -> dict[str, Any]:
    """Return all audit events attributable to the caller.

    Response shape is stable so downstream tools can rely on it.
    """
    actor = actor_id_for(x_api_key or None)
    events = collect_events(actor, _audit_path())
    return {
        "actor": actor,
        "api_key_name": getattr(request.state, "api_key_name", None),
        "audit_event_count": len(events),
        "audit_events": events,
        "notes": [
            "Feedback rows are not attributed to a specific API key and"
            " are therefore not included in this export.",
        ],
    }


@router.delete("/me")
async def delete_my_data(
    x_api_key: str = Header(default=""),
) -> JSONResponse:
    """Redact actor-identifying fields for every matching audit event.

    Returns the number of events redacted. The audit log is preserved
    in append only form; only PII fields are replaced with the literal
    string "redacted".
    """
    actor = actor_id_for(x_api_key or None)
    count = redact_actor(actor, _audit_path())
    return JSONResponse({"actor": actor, "redacted_events": count})
