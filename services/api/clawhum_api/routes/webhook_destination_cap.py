"""Per-workspace webhook destination cap administration.

Admin-only set/clear of the maximum number of registered webhook
destinations a workspace is allowed to hold. Read is open to
readers so the dashboard can show the current pin and how close
the workspace is to it. Tenant scoped on every call.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from .. import webhook_destination_cap
from ..audit import write_event
from ..auth import require_admin_with_mfa, require_api_key, require_roles
from ..tenant import current_tenant_id

router = APIRouter(
    tags=["webhook-destination-cap"],
    prefix="/webhook-destination-cap",
    dependencies=[Depends(require_api_key)],
)


def _count_live(tenant_id: str) -> int:
    """Count currently-registered hooks for a tenant.

    Imported inline to avoid a circular import between the webhooks
    routes module and this admin module at app boot.
    """
    from . import webhooks as _webhooks

    return len(_webhooks._live_hooks(tenant_id))  # type: ignore[attr-defined]


class CapResponse(BaseModel):
    enforcing: bool
    explicit: bool
    max_active: int
    effective_cap: int
    live: int
    remaining: int
    default_cap: int = webhook_destination_cap.DEFAULT_CAP
    max_allowed: int = webhook_destination_cap.MAX_CAP
    updated_at: float = 0.0
    updated_by: str = ""


class CapUpdate(BaseModel):
    max_active: int = Field(
        default=0,
        ge=0,
        le=webhook_destination_cap.MAX_CAP,
        description=(
            "Maximum number of registered webhook destinations this "
            "workspace may hold. Pass 0 to opt out of the per-workspace "
            "cap; the global hard ceiling still applies. With no "
            "policy row the legacy default cap of "
            f"{webhook_destination_cap.DEFAULT_CAP} is enforced. Hard "
            f"ceiling: {webhook_destination_cap.MAX_CAP}."
        ),
    )


def _actor_id(request: Request) -> str:
    return (
        getattr(request.state, "api_key_name", "")
        or getattr(request.state, "pat_id", "")
        or "unknown"
    )


def _to_response(tenant: str) -> CapResponse:
    pol = webhook_destination_cap.get_policy(tenant)
    eff = webhook_destination_cap.effective_cap(tenant)
    live = _count_live(tenant)
    remaining = max(0, eff - live) if eff > 0 else 0
    return CapResponse(
        enforcing=eff > 0,
        explicit=pol is not None,
        max_active=pol.max_active if pol else 0,
        effective_cap=eff,
        live=live,
        remaining=remaining,
        updated_at=pol.updated_at if pol else 0.0,
        updated_by=pol.updated_by if pol else "",
    )


@router.get(
    "",
    response_model=CapResponse,
    dependencies=[Depends(require_roles("reader"))],
)
async def get_cap(request: Request) -> CapResponse:
    return _to_response(current_tenant_id(request))


@router.put(
    "",
    response_model=CapResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def set_cap(body: CapUpdate, request: Request) -> CapResponse:
    tenant = current_tenant_id(request)
    actor = _actor_id(request)
    before = webhook_destination_cap.get_policy(tenant)
    saved = webhook_destination_cap.set_policy(
        tenant_id=tenant,
        max_active=body.max_active,
        updated_by=actor,
    )
    write_event(
        {
            "ts": saved.updated_at,
            "actor": actor,
            "tenant_id": tenant,
            "action": "webhook_destination_cap.update",
            "target": tenant,
            "request_id": getattr(request.state, "request_id", ""),
            "before": before.to_dict() if before else {"max_active": 0},
            "after": saved.to_dict(),
        }
    )
    return _to_response(tenant)
