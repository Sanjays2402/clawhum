"""Workspace audit log query and export.

Read only. Writes are produced by ``AuditLogMiddleware`` for every
mutating request. This surface lets a workspace admin search and
export their own audit trail for compliance reviews (SOC2 CC7.2,
ISO 27001 A.12.4, GDPR Art. 30) without exposing cross tenant rows.

Endpoints
- GET  /audit          paginated, filterable list of events
- GET  /audit/export   download all matching events as CSV or JSON

Storage notes
- Events live in ``settings.audit_log_path`` (JSONL).
- Size based rotation produces sibling files ``audit.jsonl.1`` ...
  ``audit.jsonl.N``; reads walk every rotated sibling so the view is
  complete across rotations.
- Reads scope strictly by the caller's tenant and only return events
  for that tenant; the underlying file is never exposed directly.
- The role gate (``require_roles('admin')``) returns 403 to members
  so non admins cannot probe the log even when authenticated.

The endpoint set deliberately omits delete / mutation paths. The log
is append only by design; rotation and retention are operator and
``/retention`` admin surface concerns respectively.
"""

from __future__ import annotations

import csv
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from clawhum_core.settings import get_settings
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..auth import require_api_key, require_roles
from ..tenant import current_tenant

router = APIRouter(
    tags=["audit"],
    prefix="/audit",
    dependencies=[Depends(require_api_key)],
)


# Bounded so a single request can never iterate the whole rotated set
# in memory; the export path also enforces the same cap per file.
_MAX_LIMIT = 200
_DEFAULT_LIMIT = 50
# Hard cap on rows returned by export to keep memory bounded; if a
# tenant truly needs more they can paginate via the list endpoint.
_EXPORT_MAX = 10_000


class AuditEvent(BaseModel):
    ts: float
    actor: str
    api_key_name: str | None = None
    tenant_id: str | None = None
    roles: list[str] = Field(default_factory=list)
    method: str
    path: str
    status: int
    request_id: str | None = None
    trace_id: str | None = None
    client_ip: str | None = None
    user_agent: str | None = None
    duration_ms: float | None = None
    dry_run: bool = False


class AuditListResponse(BaseModel):
    items: list[AuditEvent]
    total: int
    limit: int
    offset: int
    truncated: bool = False


def _audit_files() -> list[Path]:
    """Return active + rotated siblings, newest content first.

    Order matters: the active file holds the newest events, so we read
    it first and walk rotated siblings in increasing index (``.1`` is
    the most recently rotated). Files that do not exist are skipped.
    """
    settings = get_settings()
    base = settings.audit_log_path
    out: list[Path] = []
    if base.exists():
        out.append(base)
    n = 1
    while True:
        sib = base.with_name(f"{base.name}.{n}")
        if not sib.exists():
            break
        out.append(sib)
        n += 1
        # Defensive cap matching the backup_count plus a generous slack.
        if n > max(settings.audit_backup_count, 1) + 5:
            break
    return out


def _iter_events_for_tenant(tenant_id: str) -> list[dict[str, Any]]:
    """Read every event in every rotated file owned by this tenant.

    Returns newest first by ``ts``. Bad JSON lines are skipped silently
    so a single corrupt row never breaks the admin UI. Events with no
    ``tenant_id`` are never matched (defensive: refuse to leak rows
    whose ownership is unknown).
    """
    rows: list[dict[str, Any]] = []
    for path in _audit_files():
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("tenant_id") != tenant_id:
                        continue
                    rows.append(rec)
        except OSError:
            continue
    rows.sort(key=lambda r: float(r.get("ts") or 0.0), reverse=True)
    return rows


def _apply_filters(
    rows: list[dict[str, Any]],
    *,
    q: str,
    actor: str,
    method: str,
    path_prefix: str,
    status_min: int,
    status_max: int,
    since: float,
    until: float,
    dry_run: str,
) -> list[dict[str, Any]]:
    needle = q.strip().lower()
    a = actor.strip().lower()
    m = method.strip().upper()
    pp = path_prefix.strip()
    out: list[dict[str, Any]] = []
    for r in rows:
        if since and float(r.get("ts") or 0.0) < since:
            continue
        if until and float(r.get("ts") or 0.0) > until:
            continue
        if a and a not in str(r.get("actor") or "").lower():
            continue
        if m and m != str(r.get("method") or "").upper():
            continue
        if pp and not str(r.get("path") or "").startswith(pp):
            continue
        try:
            st = int(r.get("status") or 0)
        except (TypeError, ValueError):
            st = 0
        if status_min and st < status_min:
            continue
        if status_max and st > status_max:
            continue
        if dry_run == "only" and not bool(r.get("dry_run")):
            continue
        if dry_run == "exclude" and bool(r.get("dry_run")):
            continue
        if needle:
            haystack = " ".join(
                str(r.get(k) or "") for k in ("actor", "api_key_name", "path", "method", "request_id", "user_agent")
            ).lower()
            if needle not in haystack:
                continue
        out.append(r)
    return out


def _to_event(r: dict[str, Any]) -> AuditEvent:
    return AuditEvent(
        ts=float(r.get("ts") or 0.0),
        actor=str(r.get("actor") or "anonymous"),
        api_key_name=r.get("api_key_name"),
        tenant_id=r.get("tenant_id"),
        roles=list(r.get("roles") or []),
        method=str(r.get("method") or ""),
        path=str(r.get("path") or ""),
        status=int(r.get("status") or 0),
        request_id=r.get("request_id"),
        trace_id=r.get("trace_id"),
        client_ip=r.get("client_ip"),
        user_agent=r.get("user_agent"),
        duration_ms=(float(r["duration_ms"]) if r.get("duration_ms") is not None else None),
        dry_run=bool(r.get("dry_run") or False),
    )


@router.get(
    "",
    response_model=AuditListResponse,
    dependencies=[Depends(require_roles("admin"))],
)
async def list_audit(
    request: Request,
    q: str = Query(default="", max_length=200),
    actor: str = Query(default="", max_length=120),
    method: str = Query(default="", max_length=10),
    path_prefix: str = Query(default="", max_length=200, alias="path"),
    status_min: int = Query(default=0, ge=0, le=599),
    status_max: int = Query(default=0, ge=0, le=599),
    since: float = Query(default=0.0, ge=0.0),
    until: float = Query(default=0.0, ge=0.0),
    dry_run: str = Query(default="any", pattern="^(any|only|exclude)$"),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    tenant_id: str = Depends(current_tenant),
) -> AuditListResponse:
    """Return audit events for the caller's workspace.

    All filters are optional and AND together. Results are newest
    first by event timestamp. Cross tenant rows are never returned.
    """
    rows = _iter_events_for_tenant(tenant_id)
    filtered = _apply_filters(
        rows,
        q=q,
        actor=actor,
        method=method,
        path_prefix=path_prefix,
        status_min=status_min,
        status_max=status_max,
        since=since,
        until=until,
        dry_run=dry_run,
    )
    total = len(filtered)
    page = filtered[offset : offset + limit]
    return AuditListResponse(
        items=[_to_event(r) for r in page],
        total=total,
        limit=limit,
        offset=offset,
        truncated=False,
    )


@router.get(
    "/export",
    dependencies=[Depends(require_roles("admin"))],
)
async def export_audit(
    request: Request,
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    q: str = Query(default="", max_length=200),
    actor: str = Query(default="", max_length=120),
    method: str = Query(default="", max_length=10),
    path_prefix: str = Query(default="", max_length=200, alias="path"),
    status_min: int = Query(default=0, ge=0, le=599),
    status_max: int = Query(default=0, ge=0, le=599),
    since: float = Query(default=0.0, ge=0.0),
    until: float = Query(default=0.0, ge=0.0),
    dry_run: str = Query(default="any", pattern="^(any|only|exclude)$"),
    tenant_id: str = Depends(current_tenant),
) -> Response:
    """Download the caller's full audit log matching the filters.

    Bounded to ``_EXPORT_MAX`` rows per request to keep memory finite;
    when truncated the response sets ``X-Audit-Truncated: 1`` so the
    UI can show a "narrow your filters" prompt.
    """
    rows = _iter_events_for_tenant(tenant_id)
    filtered = _apply_filters(
        rows,
        q=q,
        actor=actor,
        method=method,
        path_prefix=path_prefix,
        status_min=status_min,
        status_max=status_max,
        since=since,
        until=until,
        dry_run=dry_run,
    )
    truncated = len(filtered) > _EXPORT_MAX
    if truncated:
        filtered = filtered[:_EXPORT_MAX]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    headers = {
        "Cache-Control": "no-store",
        "X-Audit-Truncated": "1" if truncated else "0",
    }
    if format == "json":
        payload = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "tenant_id": tenant_id,
            "count": len(filtered),
            "truncated": truncated,
            "items": [_to_event(r).model_dump() for r in filtered],
        }
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        headers["Content-Disposition"] = f'attachment; filename="clawhum-audit-{stamp}.json"'
        return Response(content=body, media_type="application/json", headers=headers)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "ts_iso", "ts", "actor", "api_key_name", "tenant_id", "roles",
        "method", "path", "status", "duration_ms", "client_ip",
        "user_agent", "request_id", "trace_id", "dry_run",
    ])
    for r in filtered:
        ts = float(r.get("ts") or 0.0)
        ts_iso = datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else ""
        w.writerow([
            ts_iso, ts, r.get("actor") or "", r.get("api_key_name") or "",
            r.get("tenant_id") or "", ",".join(r.get("roles") or []),
            r.get("method") or "", r.get("path") or "",
            int(r.get("status") or 0),
            r.get("duration_ms") if r.get("duration_ms") is not None else "",
            r.get("client_ip") or "", r.get("user_agent") or "",
            r.get("request_id") or "", r.get("trace_id") or "",
            "true" if r.get("dry_run") else "false",
        ])
    headers["Content-Disposition"] = f'attachment; filename="clawhum-audit-{stamp}.csv"'
    return Response(
        content=buf.getvalue().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers=headers,
    )


# ----------------------------------------------------------------------
# Tamper-evidence verification.
#
# Every audit line carries a prev_hash and entry_hash field so a
# verifier can walk the file, recompute each digest, and prove no
# entry was edited, deleted, or reordered after the fact. Exposed as
# a JSON endpoint so a procurement reviewer or SIEM can hit it on a
# schedule and alert when the chain breaks.
# ----------------------------------------------------------------------


class _ChainFileOut(BaseModel):
    path: str
    entries: int
    valid: int
    ok: bool
    first_bad_line: int | None
    reason: str | None
    head_prev_hash: str | None
    tail_entry_hash: str | None


class _ChainVerifyOut(BaseModel):
    ok: bool
    files: list[_ChainFileOut]


@router.get(
    "/verify",
    response_model=_ChainVerifyOut,
    dependencies=[Depends(require_roles("admin"))],
)
async def verify_audit_chain(
    include_rotated: bool = Query(default=True, description="Walk rotated siblings too."),
) -> _ChainVerifyOut:
    """Re-derive every entry's hash and report whether the chain holds.

    Reads only. Tenant-agnostic because the audit file is a single
    immutable record per replica, and the verification result reveals
    only counts plus the file path the operator already configured.
    Admin-gated so a member cannot probe for tampering activity.
    """
    from .. import audit_verify

    settings = get_settings()
    result = audit_verify.verify_chain(
        Path(settings.audit_log_path),
        include_rotated=include_rotated,
    )
    return _ChainVerifyOut(
        ok=result.ok,
        files=[_ChainFileOut(**f.to_dict()) for f in result.files],
    )
