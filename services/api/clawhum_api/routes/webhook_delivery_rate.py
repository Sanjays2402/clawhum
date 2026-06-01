"""Per-workspace per-hook webhook delivery rate cap administration.

Admins (with MFA step-up) set ``max_per_minute``; readers can view the
current policy so the dashboard surfaces it next to the HTTPS-only
toggle. Every mutation is appended to the audit log with before/after
state. Tenant scoped on every call; isolation is enforced by
``current_tenant_id``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import webhook_delivery_rate
from ..audit import write_event
from ..auth import require_admin_with_mfa, require_roles
from ..tenant import current_tenant_id

router = APIRouter(tags=["webhook-delivery-rate"], prefix="/webhook-delivery-rate")


class WebhookDeliveryRateResponse(BaseModel):
    max_per_minute: int = Field(
        ..., description=(
            "Per-hook ceiling on outbound delivery attempts in any 60s"
            " window. 0 means no cap (default)."
        )
    )
    ceiling: int = Field(
        ..., description=(
            "Hard upper bound the API will accept for max_per_minute."
        )
    )
    active_hook_count: int = 0
    updated_at: float = 0.0
    updated_by: str = ""


class WebhookDeliveryRateUpdate(BaseModel):
    max_per_minute: int = Field(
        ..., ge=0,
        description=(
            "Per-hook maximum outbound deliveries per minute. Set to 0"
            " to disable the cap."
        ),
    )


def _actor_id(request: Request) -> str:
    return (
        getattr(request.state, "api_key_name", "")
        or getattr(request.state, "pat_id", "")
        or "unknown"
    )


def _count_active_hooks_for(tenant_id: str) -> int:
    from .webhooks import _live_hooks  # local import to avoid cycle
    return sum(1 for h in _live_hooks(tenant_id) if h.get("active", True))


def _to_response(request: Request) -> WebhookDeliveryRateResponse:
    tenant = current_tenant_id(request)
    pol = webhook_delivery_rate.get_policy(tenant)
    return WebhookDeliveryRateResponse(
        max_per_minute=pol.max_per_minute,
        ceiling=webhook_delivery_rate.MAX_PER_MINUTE_CEILING,
        active_hook_count=_count_active_hooks_for(tenant),
        updated_at=pol.updated_at,
        updated_by=pol.updated_by,
    )


@router.get(
    "",
    response_model=WebhookDeliveryRateResponse,
    dependencies=[Depends(require_roles("reader"))],
)
async def get_webhook_delivery_rate(request: Request) -> WebhookDeliveryRateResponse:
    return _to_response(request)


@router.put(
    "",
    response_model=WebhookDeliveryRateResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def set_webhook_delivery_rate(
    body: WebhookDeliveryRateUpdate, request: Request
) -> WebhookDeliveryRateResponse:
    tenant = current_tenant_id(request)
    actor = _actor_id(request)
    before = webhook_delivery_rate.get_policy(tenant)
    try:
        saved = webhook_delivery_rate.set_policy(
            tenant_id=tenant,
            max_per_minute=body.max_per_minute,
            updated_by=actor,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail={
            "code": "webhook_delivery_rate_invalid",
            "message": str(e),
        })
    write_event(
        {
            "ts": saved.updated_at,
            "actor": actor,
            "tenant_id": tenant,
            "action": "webhook_delivery_rate.update",
            "target": tenant,
            "request_id": getattr(request.state, "request_id", ""),
            "before": before.to_dict(),
            "after": saved.to_dict(),
        }
    )
    return _to_response(request)
