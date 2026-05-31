"""Per-workspace seat license configuration.

Two endpoints:
- GET  /v1/workspace/seat-limit   read current cap and usage
- PUT  /v1/workspace/seat-limit   set the cap (admin + MFA)

Reads are open to any role on the workspace so member admins can
see the cap they are about to bump into. Writes require admin plus
a fresh MFA code, matching every other destructive workspace knob.
Mutations flow through the global audit log middleware so the change
is replayable for procurement review.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import seat_limit_store
from ..api_keys import ANON_TENANT_ID, DEV_TENANT_ID
from ..auth import require_admin_with_mfa, require_roles
from ..tenant import current_tenant_id


router = APIRouter(tags=["workspace"])


def _guard_tenant(tenant_id: str) -> str:
    if not tenant_id or tenant_id in (ANON_TENANT_ID, DEV_TENANT_ID):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="seat license requires a workspace-scoped api key",
        )
    return tenant_id


class SeatLimitView(BaseModel):
    tenant_id: str
    limit: int = Field(description="0 means unlimited")
    used: int
    remaining: int = Field(description="-1 when limit is unlimited")
    updated_by: str
    updated_at: float


class SetSeatLimitBody(BaseModel):
    limit: int = Field(ge=0, le=1_000_000)


def _view(tenant_id: str) -> dict[str, Any]:
    rec = seat_limit_store.get(tenant_id)
    limit = rec.limit if rec is not None else 0
    used = seat_limit_store.consumed_count(tenant_id)
    remaining = -1 if limit <= 0 else max(limit - used, 0)
    return {
        "tenant_id": tenant_id,
        "limit": limit,
        "used": used,
        "remaining": remaining,
        "updated_by": rec.updated_by if rec else "",
        "updated_at": rec.updated_at if rec else 0.0,
    }


@router.get(
    "/workspace/seat-limit",
    response_model=SeatLimitView,
    dependencies=[Depends(require_roles("reader"))],
)
async def get_seat_limit(request: Request) -> dict[str, Any]:
    tenant = _guard_tenant(current_tenant_id(request))
    return _view(tenant)


@router.put(
    "/workspace/seat-limit",
    response_model=SeatLimitView,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def set_seat_limit(request: Request, body: SetSeatLimitBody) -> dict[str, Any]:
    tenant = _guard_tenant(current_tenant_id(request))
    actor = getattr(request.state, "api_key_name", "unknown")
    try:
        seat_limit_store.set_limit(
            tenant_id=tenant, limit=body.limit, updated_by=actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _view(tenant)
