"""Workspace closure / wind-down administration.

Endpoints
---------
- ``GET  /workspace/closure``           Current closure status.
- ``GET  /workspace/closure/history``   Append-only timeline of closures.
- ``POST /workspace/closure``           Schedule a closure (admin + MFA).
- ``POST /workspace/closure/{id}/cancel`` Cancel a scheduled closure (admin + MFA).

While a closure is in the scheduled (grace) window, every mutating
request to other surfaces is rejected with HTTP 423 by
``auth._enforce_workspace_closure``. Once the grace window elapses
the workspace transitions to ``closed`` and every non-export route
returns HTTP 410. The closure log itself, audit, privacy, me, and mfa
stay reachable so the customer can pull data out and cancel before
the deadline.
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .. import audit as audit_log
from .. import closure as workspace_closure
from ..auth import require_api_key, require_mfa, require_roles
from ..tenant import current_tenant


router = APIRouter(
    tags=["workspace-closure"],
    prefix="/workspace/closure",
    dependencies=[Depends(require_api_key)],
)


class ClosureOut(BaseModel):
    id: str
    tenant_id: str
    reason: str
    scheduled_at: float
    scheduled_by: str
    finalize_at: float
    cancelled_at: float | None = None
    cancelled_by: str | None = None
    state: str


class StatusOut(BaseModel):
    tenant_id: str
    state: str
    closure: ClosureOut | None


class HistoryOut(BaseModel):
    tenant_id: str
    closures: list[ClosureOut]


class ScheduleIn(BaseModel):
    reason: str = Field(min_length=1, max_length=1024)
    grace_seconds: int | None = Field(
        default=None,
        ge=workspace_closure.MIN_GRACE_SECONDS,
        le=workspace_closure.MAX_GRACE_SECONDS,
        description=(
            "Grace window in seconds before the workspace transitions "
            "to closed. Defaults to 7 days."
        ),
    )


def _to_out(c: workspace_closure.Closure) -> ClosureOut:
    return ClosureOut(**c.to_dict())


def _actor(x_api_key: str | None) -> str:
    if not x_api_key:
        return "anonymous"
    return "key:" + hashlib.sha256(x_api_key.encode("utf-8")).hexdigest()[:16]


def _emit(event: str, *, tenant_id: str, actor: str, closure_id: str, extra: dict | None = None) -> None:
    body = {
        "type": event,
        "tenant_id": tenant_id,
        "actor": actor,
        "closure_id": closure_id,
    }
    if extra:
        body.update(extra)
    try:
        audit_log.write_event(body)
    except Exception:
        pass


@router.get("", response_model=StatusOut)
async def closure_status(tenant_id: str = Depends(current_tenant)) -> StatusOut:
    s = workspace_closure.status_for(tenant_id)
    c = s.get("closure")
    return StatusOut(
        tenant_id=s["tenant_id"],
        state=s["state"],
        closure=ClosureOut(**c) if c else None,
    )


@router.get(
    "/history",
    response_model=HistoryOut,
    dependencies=[Depends(require_roles("admin"))],
)
async def closure_history(tenant_id: str = Depends(current_tenant)) -> HistoryOut:
    rows = workspace_closure.list_closures(tenant_id)
    return HistoryOut(
        tenant_id=tenant_id,
        closures=[_to_out(c) for c in rows],
    )


@router.post(
    "",
    response_model=ClosureOut,
    status_code=201,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def schedule_closure(
    body: ScheduleIn,
    request: Request,
    tenant_id: str = Depends(current_tenant),
    x_api_key: str = Header(default=""),
) -> ClosureOut:
    actor = _actor(x_api_key or None)
    from ..dry_run import is_dry_run, preview
    if is_dry_run(request):
        from fastapi.responses import JSONResponse
        return JSONResponse(preview(
            "workspace_closure_schedule", None,
            tenant_id=tenant_id, actor=actor,
            reason=body.reason, grace_seconds=body.grace_seconds,
        ))
    try:
        c = workspace_closure.schedule_closure(
            tenant_id,
            reason=body.reason,
            actor=actor,
            grace_seconds=body.grace_seconds,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    _emit(
        "workspace.closure.scheduled",
        tenant_id=tenant_id,
        actor=actor,
        closure_id=c.id,
        extra={
            "reason": body.reason,
            "finalize_at": c.finalize_at,
        },
    )
    return _to_out(c)


@router.post(
    "/{closure_id}/cancel",
    response_model=ClosureOut,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def cancel_closure(
    closure_id: str,
    request: Request,
    tenant_id: str = Depends(current_tenant),
    x_api_key: str = Header(default=""),
) -> ClosureOut:
    actor = _actor(x_api_key or None)
    from ..dry_run import is_dry_run, preview
    if is_dry_run(request):
        from fastapi.responses import JSONResponse
        return JSONResponse(preview(
            "workspace_closure_cancel", closure_id,
            tenant_id=tenant_id, actor=actor,
        ))
    try:
        c = workspace_closure.cancel_closure(tenant_id, closure_id, actor=actor)
    except KeyError as e:
        raise HTTPException(404, "closure not found") from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    _emit(
        "workspace.closure.cancelled",
        tenant_id=tenant_id,
        actor=actor,
        closure_id=closure_id,
    )
    return _to_out(c)
