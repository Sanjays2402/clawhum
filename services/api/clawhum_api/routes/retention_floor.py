"""Per-workspace retention floor administration.

Admins (with MFA step-up) pin a minimum number of days each
retention category must keep. The existing ``PUT /retention``
handler refuses to lower a category below the floor (value 0,
meaning "keep forever", is always allowed because it strictly
increases retention). Reducing the floor itself is also MFA gated
and audit logged.

Tenant scoped on every call; isolation is enforced by
``current_tenant_id``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import retention_floor
from ..audit import write_event
from ..auth import require_admin_with_mfa, require_api_key, require_roles
from ..tenant import current_tenant_id

router = APIRouter(
    tags=["retention-floor"],
    prefix="/retention-floor",
    dependencies=[Depends(require_api_key)],
)


class FloorResponse(BaseModel):
    tenant_id: str
    history_days: int
    feedback_days: int
    audit_days: int
    webhook_deliveries_days: int
    updated_at: float
    updated_by: str
    ceiling: int = retention_floor.MAX_FLOOR_DAYS


class FloorUpdate(BaseModel):
    history_days: int = Field(default=0, ge=0, le=retention_floor.MAX_FLOOR_DAYS)
    feedback_days: int = Field(default=0, ge=0, le=retention_floor.MAX_FLOOR_DAYS)
    audit_days: int = Field(default=0, ge=0, le=retention_floor.MAX_FLOOR_DAYS)
    webhook_deliveries_days: int = Field(
        default=0, ge=0, le=retention_floor.MAX_FLOOR_DAYS
    )


def _actor_id(request: Request) -> str:
    return (
        getattr(request.state, "api_key_name", "")
        or getattr(request.state, "pat_id", "")
        or "unknown"
    )


def _to_response(tenant: str) -> FloorResponse:
    pol = retention_floor.get_floor(tenant)
    return FloorResponse(
        tenant_id=tenant,
        history_days=pol.history_days,
        feedback_days=pol.feedback_days,
        audit_days=pol.audit_days,
        webhook_deliveries_days=pol.webhook_deliveries_days,
        updated_at=pol.updated_at,
        updated_by=pol.updated_by,
    )


@router.get(
    "",
    response_model=FloorResponse,
    dependencies=[Depends(require_roles("reader"))],
)
async def get_floor(request: Request) -> FloorResponse:
    return _to_response(current_tenant_id(request))


@router.put(
    "",
    response_model=FloorResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def set_floor(body: FloorUpdate, request: Request) -> FloorResponse:
    tenant = current_tenant_id(request)
    actor = _actor_id(request)
    before = retention_floor.get_floor(tenant)
    try:
        saved = retention_floor.set_floor(
            tenant_id=tenant,
            history_days=body.history_days,
            feedback_days=body.feedback_days,
            audit_days=body.audit_days,
            webhook_deliveries_days=body.webhook_deliveries_days,
            updated_by=actor,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"code": "retention_floor_invalid", "message": str(e)},
        )
    write_event(
        {
            "ts": saved.updated_at,
            "actor": actor,
            "tenant_id": tenant,
            "action": "retention_floor.update",
            "target": tenant,
            "request_id": getattr(request.state, "request_id", ""),
            "before": before.to_dict(),
            "after": saved.to_dict(),
        }
    )
    return _to_response(tenant)
