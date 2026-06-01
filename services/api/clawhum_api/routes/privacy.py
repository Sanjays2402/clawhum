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
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ..auth import require_api_key, require_mfa, require_roles
from ..privacy import actor_id_for, collect_events, redact_actor, redact_tenant_feedback
from ..tenant import current_tenant_id, scope_rows
from ..workspace_export import build_export, export_filename
from .. import export_signing
from pydantic import BaseModel, Field

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
    headers = {
        "content-disposition": f'attachment; filename="{filename}"',
        "x-clawhum-export-rows": str(manifest.total_rows),
        "x-clawhum-export-sha256": manifest.sha256,
        "x-clawhum-export-tenant": tenant_id,
        "cache-control": "no-store",
    }
    if manifest.signature_hex:
        headers["x-clawhum-export-signature"] = manifest.signature_hex
        headers["x-clawhum-export-key-id"] = manifest.signature_key_id
        headers["x-clawhum-export-signature-alg"] = manifest.signature_alg
    return Response(
        content=blob,
        media_type="application/zip",
        headers=headers,
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
    from .. import legal_hold as _lh
    _active = _lh.active_hold(tenant_id)
    if _active is not None:
        raise HTTPException(
            status_code=423,
            detail={
                "error": "legal_hold_active",
                "message": "this workspace is under legal hold; destructive operations are frozen",
                "hold_id": _active.id,
                "reason": _active.reason,
            },
        )
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


class VerifyExportRequest(BaseModel):
    """Payload accepted by /workspace-export/verify.

    Callers may either upload the full manifest dict (the contents of
    ``manifest.json`` from the bundle), or supply the four discrete
    fields ``manifest_sha256``, ``generated_at``, ``key_id``, and
    ``signature``. Uploading the manifest dict is the friendlier path
    for a compliance reviewer who just wants to drag-drop the file.
    """

    manifest: dict[str, Any] | None = Field(
        default=None,
        description="Full manifest.json dict from the export bundle.",
    )
    signature: str | None = Field(default=None, description="Hex signature.")
    key_id: str | None = Field(default=None)
    manifest_sha256: str | None = Field(default=None)
    generated_at: float | None = Field(default=None)


@router.post("/workspace-export/verify")
async def verify_workspace_export(
    body: VerifyExportRequest,
    request: Request,
) -> JSONResponse:
    """Verify a workspace export signature without re-downloading the bundle.

    Open to any authenticated caller in the tenant; verification leaks
    no signing material. The endpoint is strictly tenant-scoped: a
    signature for tenant A's bundle never verifies as tenant B's,
    because we only look up the signing key for the caller's tenant.
    Returns 200 with ``{"valid": true, ...}`` on success, 400 with
    ``{"valid": false, "reason": "..."}`` on any failure mode.
    """
    tenant_id = current_tenant_id(request)

    manifest = body.manifest or {}
    sha = body.manifest_sha256 or str(manifest.get("sha256") or "")
    gen = body.generated_at
    if gen is None:
        gen = manifest.get("generated_at")
    sig_block = manifest.get("signature") if isinstance(manifest, dict) else None
    if isinstance(sig_block, dict):
        key_id = body.key_id or str(sig_block.get("key_id") or "")
        sig = body.signature or str(sig_block.get("value") or "")
    else:
        key_id = body.key_id or ""
        sig = body.signature or ""

    if not sha or gen is None or not key_id or not sig:
        return JSONResponse(
            status_code=400,
            content={
                "valid": False,
                "reason": "missing one of manifest_sha256, generated_at, key_id, signature",
            },
        )

    # Reject cross-tenant: if the manifest itself claims a different
    # tenant_id, fail fast and loudly so a misfiled bundle is obvious.
    claimed_tenant = str(manifest.get("tenant_id") or tenant_id)
    if claimed_tenant and claimed_tenant != tenant_id:
        return JSONResponse(
            status_code=400,
            content={
                "valid": False,
                "reason": "manifest tenant_id does not match caller tenant",
                "manifest_tenant_id": claimed_tenant,
                "caller_tenant_id": tenant_id,
            },
        )

    try:
        generated_at = float(gen)
    except (TypeError, ValueError):
        return JSONResponse(
            status_code=400,
            content={"valid": False, "reason": "generated_at must be numeric"},
        )

    ok = export_signing.verify_export(
        tenant_id=tenant_id,
        key_id=key_id,
        signature_hex=sig,
        manifest_sha256=sha,
        generated_at=generated_at,
    )
    if not ok:
        return JSONResponse(
            status_code=400,
            content={
                "valid": False,
                "reason": "signature does not match active or grace key",
            },
        )
    key = export_signing.get_key(tenant_id)
    return JSONResponse({
        "valid": True,
        "tenant_id": tenant_id,
        "key_id": key_id,
        "algorithm": export_signing.SIGNING_ALG,
        "is_active_key": bool(key and key.key_id == key_id),
        "verified_at": int(__import__("time").time()),
    })
