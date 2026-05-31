"""Per-workspace data residency administration."""

from __future__ import annotations

from clawhum_core.settings import get_settings
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import residency_store
from ..audit import write_event
from ..auth import require_admin_with_mfa, require_roles
from ..tenant import current_tenant

router = APIRouter(tags=["residency"], prefix="/residency")


class ResidencyOut(BaseModel):
    tenant_id: str
    region: str
    enforce: bool
    updated_at: float
    updated_by: str


class ResidencyReadResponse(BaseModel):
    residency: ResidencyOut
    node_region: str
    enforcement: bool
    available_regions: list[str]


class ResidencyUpdate(BaseModel):
    region: str = Field(min_length=1, max_length=16)
    enforce: bool = False


def _to_out(r: residency_store.Residency) -> ResidencyOut:
    return ResidencyOut(
        tenant_id=r.tenant_id,
        region=r.region,
        enforce=r.enforce,
        updated_at=r.updated_at,
        updated_by=r.updated_by,
    )


@router.get(
    "",
    response_model=ResidencyReadResponse,
    dependencies=[Depends(require_roles("admin"))],
)
async def read_residency(
    tenant_id: str = Depends(current_tenant),
) -> ResidencyReadResponse:
    settings = get_settings()
    return ResidencyReadResponse(
        residency=_to_out(residency_store.get(tenant_id)),
        node_region=(settings.region or "unset").lower(),
        enforcement=bool(settings.residency_enforcement),
        available_regions=sorted(residency_store.VALID_REGIONS),
    )


@router.put(
    "",
    response_model=ResidencyOut,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def update_residency(
    body: ResidencyUpdate,
    request: Request,
    tenant_id: str = Depends(current_tenant),
) -> ResidencyOut:
    region = (body.region or "unset").lower()
    if region not in residency_store.VALID_REGIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown region; allowed: {sorted(residency_store.VALID_REGIONS)}",
        )
    before = residency_store.get(tenant_id)
    actor = getattr(request.state, "api_key_name", "") or "admin"
    saved = residency_store.set_(
        tenant_id=tenant_id,
        region=region,
        enforce=body.enforce,
        actor=actor,
    )
    write_event(
        {
            "ts": saved.updated_at,
            "actor": actor,
            "tenant_id": tenant_id,
            "action": "residency.update",
            "target": tenant_id,
            "request_id": getattr(request.state, "request_id", ""),
            "before": before.to_dict(),
            "after": saved.to_dict(),
        }
    )
    return _to_out(saved)
