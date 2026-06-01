"""Per-workspace webhook auto-disable threshold administration.

Admin-only set/clear of the per-workspace override for the webhook
circuit-breaker threshold. Read is open to readers so the dashboard
can show the current pin and the effective value. Tenant scoped on
every call; mutations require step-up MFA and write an audit event.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from .. import webhook_auto_disable_policy
from ..audit import write_event
from ..auth import require_admin_with_mfa, require_api_key, require_roles
from ..tenant import current_tenant_id

router = APIRouter(
    tags=["webhook-auto-disable-policy"],
    prefix="/webhook-auto-disable-policy",
    dependencies=[Depends(require_api_key)],
)


class PolicyResponse(BaseModel):
    explicit: bool
    threshold: int
    effective_threshold: int
    breaker_enabled: bool
    global_default: int
    max_threshold: int = webhook_auto_disable_policy.MAX_THRESHOLD
    updated_at: float = 0.0
    updated_by: str = ""


class PolicyUpdate(BaseModel):
    threshold: int = Field(
        default=0,
        ge=0,
        le=webhook_auto_disable_policy.MAX_THRESHOLD,
        description=(
            "Consecutive failed deliveries before a webhook is "
            "auto-disabled for this workspace. Pass 0 to disable the "
            "circuit breaker for this workspace (manual pause only). "
            "With no policy row the deployment-wide default applies. "
            f"Hard ceiling: {webhook_auto_disable_policy.MAX_THRESHOLD}."
        ),
    )


def _actor_id(request: Request) -> str:
    return (
        getattr(request.state, "api_key_name", "")
        or getattr(request.state, "pat_id", "")
        or "unknown"
    )


def _to_response(tenant: str) -> PolicyResponse:
    pol = webhook_auto_disable_policy.get_policy(tenant)
    eff = webhook_auto_disable_policy.effective_threshold(tenant)
    return PolicyResponse(
        explicit=pol is not None,
        threshold=pol.threshold if pol else 0,
        effective_threshold=eff,
        breaker_enabled=eff > 0,
        global_default=webhook_auto_disable_policy._global_default(),
        updated_at=pol.updated_at if pol else 0.0,
        updated_by=pol.updated_by if pol else "",
    )


@router.get(
    "",
    response_model=PolicyResponse,
    dependencies=[Depends(require_roles("reader"))],
)
async def get_policy(request: Request) -> PolicyResponse:
    return _to_response(current_tenant_id(request))


@router.put(
    "",
    response_model=PolicyResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def set_policy(body: PolicyUpdate, request: Request) -> PolicyResponse:
    tenant = current_tenant_id(request)
    actor = _actor_id(request)
    before = webhook_auto_disable_policy.get_policy(tenant)
    saved = webhook_auto_disable_policy.set_policy(
        tenant_id=tenant,
        threshold=body.threshold,
        updated_by=actor,
    )
    write_event(
        {
            "ts": saved.updated_at,
            "actor": actor,
            "tenant_id": tenant,
            "action": "webhook_auto_disable_policy.update",
            "target": tenant,
            "request_id": getattr(request.state, "request_id", ""),
            "before": before.to_dict() if before else {"threshold": 0},
            "after": saved.to_dict(),
        }
    )
    return _to_response(tenant)
