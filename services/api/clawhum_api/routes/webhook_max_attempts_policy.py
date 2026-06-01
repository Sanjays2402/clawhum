"""Per-workspace webhook max-attempts policy administration.

Admins (with MFA step-up) set ``max_attempts``; readers can view the
current policy so the dashboard surfaces it alongside the other
outbound webhook controls (HTTPS-only, breaker threshold, delivery
rate cap). Every mutation appends a structured audit event with
before/after state. Tenant scoped on every call; isolation is
enforced by ``current_tenant_id``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import webhook_max_attempts_policy
from ..audit import write_event
from ..auth import require_admin_with_mfa, require_api_key, require_roles
from ..tenant import current_tenant_id

router = APIRouter(
    tags=["webhook-max-attempts-policy"],
    prefix="/webhook-max-attempts-policy",
    dependencies=[Depends(require_api_key)],
)


class PolicyResponse(BaseModel):
    explicit: bool
    max_attempts: int = Field(
        ..., description=(
            "Workspace pin for the maximum outbound delivery attempts"
            " per event. 0 in this field means no explicit pin (the"
            " global default applies)."
        ),
    )
    effective_max_attempts: int = Field(
        ..., description=(
            "Attempt count the dispatcher will actually use for this"
            " workspace right now."
        ),
    )
    global_default: int
    ceiling: int = webhook_max_attempts_policy.MAX_ATTEMPTS_CEILING
    updated_at: float = 0.0
    updated_by: str = ""


class PolicyUpdate(BaseModel):
    max_attempts: int = Field(
        ...,
        ge=1,
        le=webhook_max_attempts_policy.MAX_ATTEMPTS_CEILING,
        description=(
            "Maximum outbound delivery attempts per event for this"
            " workspace. Must be at least 1 so an event is never"
            " silently dropped. Hard ceiling:"
            f" {webhook_max_attempts_policy.MAX_ATTEMPTS_CEILING}."
        ),
    )


def _actor_id(request: Request) -> str:
    return (
        getattr(request.state, "api_key_name", "")
        or getattr(request.state, "pat_id", "")
        or "unknown"
    )


def _to_response(tenant: str) -> PolicyResponse:
    pol = webhook_max_attempts_policy.get_policy(tenant)
    return PolicyResponse(
        explicit=pol is not None,
        max_attempts=pol.max_attempts if pol else 0,
        effective_max_attempts=webhook_max_attempts_policy.effective_max_attempts(tenant),
        global_default=webhook_max_attempts_policy._global_default(),
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
    before = webhook_max_attempts_policy.get_policy(tenant)
    try:
        saved = webhook_max_attempts_policy.set_policy(
            tenant_id=tenant,
            max_attempts=body.max_attempts,
            updated_by=actor,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail={
            "code": "webhook_max_attempts_invalid",
            "message": str(e),
        })
    write_event(
        {
            "ts": saved.updated_at,
            "actor": actor,
            "tenant_id": tenant,
            "action": "webhook_max_attempts_policy.update",
            "target": tenant,
            "request_id": getattr(request.state, "request_id", ""),
            "before": before.to_dict() if before else {"max_attempts": 0},
            "after": saved.to_dict(),
        }
    )
    return _to_response(tenant)
