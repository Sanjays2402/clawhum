"""Per-workspace quota plan administration.

Admin-only read/update of the workspace's plan. The plan caps aggregate
traffic across every key in the workspace, on top of the per-key RPM
limits configured on each API key. See ``quota_store`` for the
storage shape.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..audit import write_event
from ..auth import require_admin_with_mfa, require_roles
from ..tenant import current_tenant
from .. import quota_store

router = APIRouter(tags=["quotas"], prefix="/quotas")


class PlanOut(BaseModel):
    tenant_id: str
    plan: str
    rpm_ceiling: int
    daily_quota: int
    updated_at: float
    updated_by: str


class PlanCatalogEntry(BaseModel):
    name: str
    rpm_ceiling: int
    daily_quota: int


class PlanReadResponse(BaseModel):
    plan: PlanOut
    catalog: list[PlanCatalogEntry]


class PlanUpdate(BaseModel):
    plan: str = Field(min_length=1, max_length=32)
    rpm_ceiling: int = Field(default=0, ge=0, le=10_000_000)
    daily_quota: int = Field(default=0, ge=0, le=10_000_000_000)


def _to_out(p: quota_store.Plan) -> PlanOut:
    return PlanOut(
        tenant_id=p.tenant_id,
        plan=p.plan,
        rpm_ceiling=p.rpm_ceiling,
        daily_quota=p.daily_quota,
        updated_at=p.updated_at,
        updated_by=p.updated_by,
    )


def _catalog() -> list[PlanCatalogEntry]:
    return [
        PlanCatalogEntry(name=name, rpm_ceiling=rpm, daily_quota=day)
        for name, (rpm, day) in quota_store.PLAN_DEFAULTS.items()
    ]


@router.get(
    "",
    response_model=PlanReadResponse,
    dependencies=[Depends(require_roles("admin"))],
)
async def read_plan(tenant_id: str = Depends(current_tenant)) -> PlanReadResponse:
    plan = quota_store.get_plan(tenant_id)
    return PlanReadResponse(plan=_to_out(plan), catalog=_catalog())


@router.put(
    "",
    response_model=PlanOut,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def update_plan(
    body: PlanUpdate,
    request: Request,
    tenant_id: str = Depends(current_tenant),
) -> PlanOut:
    if body.plan not in quota_store.VALID_PLANS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown plan; allowed: {sorted(quota_store.VALID_PLANS)}",
        )
    before = quota_store.get_plan(tenant_id)
    actor = getattr(request.state, "api_key_name", "") or "admin"
    saved = quota_store.set_plan(
        tenant_id=tenant_id,
        plan=body.plan,
        rpm_ceiling=body.rpm_ceiling,
        daily_quota=body.daily_quota,
        actor=actor,
    )
    write_event(
        {
            "ts": saved.updated_at,
            "actor": actor,
            "tenant_id": tenant_id,
            "action": "quota.plan.update",
            "target": tenant_id,
            "request_id": getattr(request.state, "request_id", ""),
            "before": before.to_dict(),
            "after": saved.to_dict(),
        }
    )
    return _to_out(saved)
