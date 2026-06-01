"""Per-workspace PAT minimum-requirements administration.

Admin-only set of the floor security attributes any new PAT mint
must satisfy in this workspace (identifiable owner, bounded expiry,
IP CIDR scope). Read is open to readers so the dashboard can show
the current pin alongside the create form. Tenant scoped on every
call so a workspace cannot read or alter another workspace's
policy.

Maps to SOC2 CC6.1 and ISO 27001 A.9.2.1 evidence requests.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from .. import pat_min_requirements
from ..audit import write_event
from ..auth import require_admin_with_mfa, require_roles
from ..tenant import current_tenant_id

router = APIRouter(tags=["pat-min-requirements"], prefix="/pat-min-requirements")


class PolicyResponse(BaseModel):
    enforcing: bool
    require_owner_email: bool
    require_expiry: bool
    max_expiry_days: int
    require_ip_cidrs: bool
    max_expiry_days_ceiling: int
    updated_at: float = 0.0
    updated_by: str = ""


class PolicyUpdate(BaseModel):
    require_owner_email: bool = Field(
        default=False,
        description="Reject PAT mints without a non blank owner_email.",
    )
    require_expiry: bool = Field(
        default=False,
        description=(
            "Reject PAT mints without a positive expires_in_days. "
            "Combine with max_expiry_days to cap how long any token "
            "can live."
        ),
    )
    max_expiry_days: int = Field(
        default=0,
        ge=0,
        le=pat_min_requirements.MAX_EXPIRY_DAYS,
        description=(
            "Largest expires_in_days the workspace will mint. 0 means "
            "no upper bound (only applied when require_expiry is on)."
        ),
    )
    require_ip_cidrs: bool = Field(
        default=False,
        description=(
            "Reject PAT mints with no ip_cidrs entries so every token "
            "is network scoped at issue time."
        ),
    )


def _actor_id(request: Request) -> str:
    return (
        getattr(request.state, "api_key_name", "")
        or getattr(request.state, "pat_id", "")
        or "unknown"
    )


def _to_response(request: Request) -> PolicyResponse:
    tenant = current_tenant_id(request)
    pol = pat_min_requirements.effective(tenant)
    return PolicyResponse(
        enforcing=pol.enforcing,
        require_owner_email=pol.require_owner_email,
        require_expiry=pol.require_expiry,
        max_expiry_days=pol.max_expiry_days,
        require_ip_cidrs=pol.require_ip_cidrs,
        max_expiry_days_ceiling=pat_min_requirements.MAX_EXPIRY_DAYS,
        updated_at=pol.updated_at,
        updated_by=pol.updated_by,
    )


@router.get(
    "",
    response_model=PolicyResponse,
    dependencies=[Depends(require_roles("reader"))],
)
async def get_pat_min_requirements(request: Request) -> PolicyResponse:
    return _to_response(request)


@router.put(
    "",
    response_model=PolicyResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def set_pat_min_requirements(
    body: PolicyUpdate, request: Request
) -> PolicyResponse:
    tenant = current_tenant_id(request)
    actor = _actor_id(request)
    before = pat_min_requirements.get_policy(tenant)
    saved = pat_min_requirements.set_policy(
        tenant_id=tenant,
        require_owner_email=body.require_owner_email,
        require_expiry=body.require_expiry,
        max_expiry_days=body.max_expiry_days,
        require_ip_cidrs=body.require_ip_cidrs,
        updated_by=actor,
    )
    write_event(
        {
            "ts": saved.updated_at,
            "actor": actor,
            "tenant_id": tenant,
            "action": "pat_min_requirements.update",
            "target": tenant,
            "request_id": getattr(request.state, "request_id", ""),
            "before": before.to_dict() if before else {
                "require_owner_email": False,
                "require_expiry": False,
                "max_expiry_days": 0,
                "require_ip_cidrs": False,
            },
            "after": saved.to_dict(),
        }
    )
    return _to_response(request)
