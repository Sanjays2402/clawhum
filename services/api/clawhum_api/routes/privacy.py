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
from clawhum_library.feedback import read_feedback
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, Response

from ..auth import require_api_key, require_mfa, require_roles
from ..privacy import actor_id_for, collect_events, redact_actor, redact_tenant_feedback
from ..tenant import current_tenant_id, scope_rows
from ..workspace_export import build_export, export_filename

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
    tenant_id = current_tenant_id(request)
    feedback_rows = scope_rows(read_feedback(get_settings().feedback_path), tenant_id)
    return {
        "actor": actor,
        "api_key_name": getattr(request.state, "api_key_name", None),
        "tenant_id": tenant_id,
        "audit_event_count": len(events),
        "audit_events": events,
        "feedback_row_count": len(feedback_rows),
        "feedback_rows": feedback_rows,
        "notes": [
            "Feedback rows are scoped by tenant_id. Rows written before"
            " multi tenancy was enabled have no tenant tag and are only"
            " surfaced to the 'default' tenant.",
        ],
    }


@router.get(
    "/workspace-export",
    dependencies=[Depends(require_roles("admin"))],
)
async def workspace_export(request: Request) -> Any:
    """Workspace-wide GDPR/SOC2 data portability bundle.

    Admin-only. Streams a ZIP containing every tenant-scoped store
    (history, feedback, audit, webhooks, members, retention, SSO,
    IP allowlist, quotas, PATs) plus a manifest with row counts and
    a sha256 over the payloads. Secrets are redacted.

    Set ``Accept: application/json`` (or ``?format=json``) for a
    machine-readable summary without downloading the ZIP, useful for
    dry-runs and procurement review checklists.
    """
    tenant_id = current_tenant_id(request)
    blob, manifest = build_export(tenant_id)
    fmt = (request.query_params.get("format") or "").lower()
    accept = request.headers.get("accept", "")
    if fmt == "json" or ("application/json" in accept and "application/zip" not in accept):
        return JSONResponse({
            "manifest": manifest.to_dict(),
            "size_bytes": len(blob),
            "filename": export_filename(tenant_id, manifest.generated_at),
        })
    filename = export_filename(tenant_id, manifest.generated_at)
    return Response(
        content=blob,
        media_type="application/zip",
        headers={
            "content-disposition": f'attachment; filename="{filename}"',
            "x-clawhum-export-rows": str(manifest.total_rows),
            "x-clawhum-export-sha256": manifest.sha256,
            "x-clawhum-export-tenant": tenant_id,
            "cache-control": "no-store",
        },
    )


@router.delete("/me", dependencies=[Depends(require_roles("admin")), Depends(require_mfa())])
async def delete_my_data(
    request: Request,
    x_api_key: str = Header(default=""),
) -> JSONResponse:
    """Redact actor-identifying fields for every matching audit event.

    Returns the number of events redacted in the audit log and the
    number of feedback rows scrubbed for the caller's tenant. The audit
    log is preserved in append only form; only PII fields are replaced
    with the literal string "redacted". Feedback rows for the tenant
    have their identifiers redacted while the row shape is preserved so
    aggregate analytics remain valid.
    """
    actor = actor_id_for(x_api_key or None)
    tenant_id = current_tenant_id(request)
    from ..dry_run import is_dry_run, preview
    if is_dry_run(request):
        events = collect_events(actor, _audit_path())
        feedback_rows = scope_rows(
            read_feedback(get_settings().feedback_path), tenant_id
        )
        return JSONResponse(preview(
            "privacy_erasure", None,
            tenant_id=tenant_id,
            actor=actor,
            would_redact_audit_events=len(events),
            would_redact_feedback_rows=len(feedback_rows),
            warnings=["This is irreversible. Re-run without dry_run to apply."],
        ))
    count = redact_actor(actor, _audit_path())
    feedback_redacted = redact_tenant_feedback(tenant_id, get_settings().feedback_path)
    return JSONResponse({
        "actor": actor,
        "tenant_id": tenant_id,
        "redacted_events": count,
        "redacted_feedback_rows": feedback_redacted,
    })
