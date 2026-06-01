"""Per-workspace PAT concurrency cap administration.

Admin-only set/clear of the maximum number of live, non-expired PATs
this workspace is allowed to hold at once. Read is open to readers so
the dashboard can show the current pin and how close the workspace is
to it. Tenant scoped on every call.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from .. import pat_concurrency
from ..audit import write_event
from ..auth import require_admin_with_mfa, require_api_key, require_roles
from ..tenant import current_tenant_id

router = APIRouter(
    tags=["pat-concurrency"],
    prefix="/pat-concurrency",
    dependencies=[Depends(require_api_key)],
)


class ConcurrencyResponse(BaseModel):
    enforcing: bool
    max_active: int
    live: int
    remaining: int
    max_allowed: int = pat_concurrency.MAX_CAP
    updated_at: float = 0.0
    updated_by: str = ""


class ConcurrencyUpdate(BaseModel):
    max_active: int = Field(
        default=0,
        ge=0,
        le=pat_concurrency.MAX_CAP,
        description=(
            "Maximum number of live PATs this workspace may hold. "
            "Pass 0 to clear the cap and return to 'no restriction'. "
            f"Hard ceiling: {pat_concurrency.MAX_CAP}."
        ),
    )


def _actor_id(request: Request) -> str:
    return (
        getattr(request.state, "api_key_name", "")
        or getattr(request.state, "pat_id", "")
        or "unknown"
    )


def _to_response(tenant: str) -> ConcurrencyResponse:
    pol = pat_concurrency.get_policy(tenant)
    cap = pol.max_active if pol else 0
    live = pat_concurrency.count_active(tenant)
    remaining = max(0, cap - live) if cap > 0 else 0
    return ConcurrencyResponse(
        enforcing=bool(pol and pol.max_active > 0),
        max_active=cap,
        live=live,
        remaining=remaining,
        updated_at=pol.updated_at if pol else 0.0,
        updated_by=pol.updated_by if pol else "",
    )


@router.get(
    "",
    response_model=ConcurrencyResponse,
    dependencies=[Depends(require_roles("reader"))],
)
async def get_concurrency(request: Request) -> ConcurrencyResponse:
    return _to_response(current_tenant_id(request))


@router.put(
    "",
    response_model=ConcurrencyResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def set_concurrency(
    body: ConcurrencyUpdate, request: Request
) -> ConcurrencyResponse:
    tenant = current_tenant_id(request)
    actor = _actor_id(request)
    before = pat_concurrency.get_policy(tenant)
    saved = pat_concurrency.set_policy(
        tenant_id=tenant,
        max_active=body.max_active,
        updated_by=actor,
    )
    write_event(
        {
            "ts": saved.updated_at,
            "actor": actor,
            "tenant_id": tenant,
            "action": "pat_concurrency.update",
            "target": tenant,
            "request_id": getattr(request.state, "request_id", ""),
            "before": before.to_dict() if before else {"max_active": 0},
            "after": saved.to_dict(),
        }
    )
    return _to_response(tenant)
