"""Per-workspace monthly budget cap administration.

Admin-only read/update of the workspace's monthly spend cap. The cap
bounds chargeable consumption over a rolling 30 day window across
every key and PAT in the workspace. The cap is independent of the
rate-limit plan (``/quotas``) which bounds the *rate*; this bounds
the *month*.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..audit import write_event
from ..auth import require_admin_with_mfa, require_roles
from ..tenant import current_tenant
from ..usage import month_count
from .. import budget_store

router = APIRouter(tags=["budget"], prefix="/budget")


class BudgetOut(BaseModel):
    tenant_id: str
    monthly_cap: int
    soft_threshold_pct: int
    hard_stop: bool
    notes: str
    updated_at: float
    updated_by: str


class BudgetStatus(BaseModel):
    budget: BudgetOut
    used: int
    remaining: int
    percent_used: float
    status: str  # ok | warning | exhausted | unset
    window_sec: int = 86_400 * 30


class BudgetUpdate(BaseModel):
    monthly_cap: int = Field(default=0, ge=0, le=1_000_000_000)
    soft_threshold_pct: int = Field(default=80, ge=0, le=100)
    hard_stop: bool = True
    notes: str = Field(default="", max_length=280)


def _to_out(b: budget_store.Budget) -> BudgetOut:
    return BudgetOut(
        tenant_id=b.tenant_id,
        monthly_cap=b.monthly_cap,
        soft_threshold_pct=b.soft_threshold_pct,
        hard_stop=b.hard_stop,
        notes=b.notes,
        updated_at=b.updated_at,
        updated_by=b.updated_by,
    )


def _status(b: budget_store.Budget, used: int) -> BudgetStatus:
    cap = b.monthly_cap
    if cap <= 0:
        label = "unset"
        remaining = 0
        pct = 0.0
    else:
        remaining = max(0, cap - used)
        pct = round((used / cap) * 100.0, 2)
        if remaining == 0:
            label = "exhausted"
        elif b.soft_threshold_pct > 0 and pct >= b.soft_threshold_pct:
            label = "warning"
        else:
            label = "ok"
    return BudgetStatus(
        budget=_to_out(b),
        used=used,
        remaining=remaining,
        percent_used=pct,
        status=label,
    )


@router.get(
    "",
    response_model=BudgetStatus,
    dependencies=[Depends(require_roles("admin"))],
)
async def read_budget(tenant_id: str = Depends(current_tenant)) -> BudgetStatus:
    return _status(budget_store.get_budget(tenant_id), month_count(tenant_id))


@router.put(
    "",
    response_model=BudgetStatus,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def update_budget(
    body: BudgetUpdate,
    request: Request,
    tenant_id: str = Depends(current_tenant),
) -> BudgetStatus:
    before = budget_store.get_budget(tenant_id)
    actor = getattr(request.state, "api_key_name", "") or "admin"
    saved = budget_store.set_budget(
        tenant_id=tenant_id,
        monthly_cap=body.monthly_cap,
        soft_threshold_pct=body.soft_threshold_pct,
        hard_stop=body.hard_stop,
        notes=body.notes,
        actor=actor,
    )
    write_event(
        {
            "ts": saved.updated_at,
            "actor": actor,
            "tenant_id": tenant_id,
            "action": "budget.cap.update",
            "target": tenant_id,
            "request_id": getattr(request.state, "request_id", ""),
            "before": before.to_dict(),
            "after": saved.to_dict(),
        }
    )
    return _status(saved, month_count(tenant_id))
