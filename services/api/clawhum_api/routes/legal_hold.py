"""Workspace legal hold administration."""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .. import audit as audit_log
from .. import legal_hold
from ..auth import require_api_key, require_mfa, require_roles
from ..tenant import current_tenant


router = APIRouter(
    tags=["legal-holds"],
    prefix="/legal-holds",
    dependencies=[Depends(require_api_key)],
)


class HoldOut(BaseModel):
    id: str
    tenant_id: str
    reason: str
    created_at: float
    created_by: str
    released_at: float | None = None
    released_by: str | None = None
    active: bool


class HoldListOut(BaseModel):
    tenant_id: str
    on_hold: bool
    active_hold_id: str | None
    holds: list[HoldOut]


class StatusOut(BaseModel):
    tenant_id: str
    on_hold: bool
    active_hold_id: str | None
    reason: str | None


class PlaceHoldIn(BaseModel):
    reason: str = Field(min_length=1, max_length=1024)


def _to_out(h: legal_hold.LegalHold) -> HoldOut:
    return HoldOut(
        id=h.id, tenant_id=h.tenant_id, reason=h.reason,
        created_at=h.created_at, created_by=h.created_by,
        released_at=h.released_at, released_by=h.released_by,
        active=h.active,
    )


def _actor(x_api_key: str | None) -> str:
    if not x_api_key:
        return "anonymous"
    return "key:" + hashlib.sha256(x_api_key.encode("utf-8")).hexdigest()[:16]


def _emit(event: str, *, tenant_id: str, actor: str, hold_id: str, extra: dict | None = None) -> None:
    body = {"type": event, "tenant_id": tenant_id, "actor": actor, "hold_id": hold_id}
    if extra:
        body.update(extra)
    try:
        audit_log.write_event(body)
    except Exception:
        pass


@router.get(
    "",
    response_model=HoldListOut,
    dependencies=[Depends(require_roles("admin"))],
)
async def list_my_holds(tenant_id: str = Depends(current_tenant)) -> HoldListOut:
    holds = legal_hold.list_holds(tenant_id)
    active = legal_hold.active_hold(tenant_id)
    return HoldListOut(
        tenant_id=tenant_id,
        on_hold=active is not None,
        active_hold_id=active.id if active else None,
        holds=[_to_out(h) for h in holds],
    )


@router.get("/status", response_model=StatusOut)
async def hold_status(tenant_id: str = Depends(current_tenant)) -> StatusOut:
    active = legal_hold.active_hold(tenant_id)
    return StatusOut(
        tenant_id=tenant_id,
        on_hold=active is not None,
        active_hold_id=active.id if active else None,
        reason=active.reason if active else None,
    )


@router.post(
    "",
    response_model=HoldOut,
    status_code=201,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def place_my_hold(
    body: PlaceHoldIn,
    request: Request,
    tenant_id: str = Depends(current_tenant),
    x_api_key: str = Header(default=""),
) -> HoldOut:
    actor = _actor(x_api_key or None)
    from ..dry_run import is_dry_run, preview
    if is_dry_run(request):
        from fastapi.responses import JSONResponse
        return JSONResponse(preview(
            "legal_hold_place", None,
            tenant_id=tenant_id, actor=actor, reason=body.reason,
        ))
    try:
        hold = legal_hold.place_hold(tenant_id, reason=body.reason, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    _emit("legal_hold.placed", tenant_id=tenant_id, actor=actor, hold_id=hold.id, extra={"reason": body.reason})
    return _to_out(hold)


@router.post(
    "/{hold_id}/release",
    response_model=HoldOut,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def release_my_hold(
    hold_id: str,
    request: Request,
    tenant_id: str = Depends(current_tenant),
    x_api_key: str = Header(default=""),
) -> HoldOut:
    actor = _actor(x_api_key or None)
    from ..dry_run import is_dry_run, preview
    if is_dry_run(request):
        from fastapi.responses import JSONResponse
        return JSONResponse(preview(
            "legal_hold_release", hold_id, tenant_id=tenant_id, actor=actor,
        ))
    try:
        released = legal_hold.release_hold(tenant_id, hold_id, actor=actor)
    except KeyError as e:
        raise HTTPException(404, "hold not found") from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    _emit("legal_hold.released", tenant_id=tenant_id, actor=actor, hold_id=hold_id)
    return _to_out(released)
