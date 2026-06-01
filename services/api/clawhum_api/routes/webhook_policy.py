"""Per-workspace HTTPS-only webhook policy administration.

Admins (with MFA step-up) toggle ``require_https`` for the workspace.
Readers can read the current policy so the dashboard can show it.
Every mutation is appended to the audit log with before/after state.
Tenant scoped on every call; isolation is enforced by ``current_tenant_id``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from .. import webhook_policy
from ..audit import write_event
from ..auth import require_admin_with_mfa, require_roles
from ..tenant import current_tenant_id

router = APIRouter(tags=["webhook-policy"], prefix="/webhook-policy")


class WebhookPolicyResponse(BaseModel):
    require_https: bool
    min_tls_version: str = ""
    plaintext_endpoint_count: int = 0
    allowed_min_tls_versions: list[str] = Field(
        default_factory=lambda: ["", "1.2", "1.3"]
    )
    updated_at: float = 0.0
    updated_by: str = ""


class WebhookPolicyUpdate(BaseModel):
    require_https: bool = Field(
        ..., description=(
            "When true, every webhook registration and every delivery "
            "attempt rejects plaintext http:// destinations."
        )
    )
    min_tls_version: str = Field(
        "", description=(
            "Minimum negotiated TLS version for outbound deliveries. "
            "One of \"\", \"1.2\", or \"1.3\". Setting any value here "
            "implicitly also requires https."
        )
    )


def _actor_id(request: Request) -> str:
    return (
        getattr(request.state, "api_key_name", "")
        or getattr(request.state, "pat_id", "")
        or "unknown"
    )


def _count_plaintext_for(tenant_id: str) -> int:
    """Count active webhooks in the tenant whose URL is plaintext.

    Used to warn admins how many existing deliveries will start
    failing once enforcement flips on.
    """
    from .webhooks import _live_hooks  # local import to avoid cycle
    n = 0
    for h in _live_hooks(tenant_id):
        url = str(h.get("url", "")).lower()
        if url.startswith("http://"):
            n += 1
    return n


def _to_response(request: Request) -> WebhookPolicyResponse:
    tenant = current_tenant_id(request)
    pol = webhook_policy.get_policy(tenant)
    return WebhookPolicyResponse(
        require_https=pol.require_https,
        min_tls_version=pol.min_tls_version,
        plaintext_endpoint_count=_count_plaintext_for(tenant),
        allowed_min_tls_versions=sorted(
            webhook_policy.ALLOWED_TLS_VERSIONS, key=lambda v: (v != "", v)
        ),
        updated_at=pol.updated_at,
        updated_by=pol.updated_by,
    )


@router.get(
    "",
    response_model=WebhookPolicyResponse,
    dependencies=[Depends(require_roles("reader"))],
)
async def get_webhook_policy(request: Request) -> WebhookPolicyResponse:
    return _to_response(request)


@router.put(
    "",
    response_model=WebhookPolicyResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def set_webhook_policy(
    body: WebhookPolicyUpdate, request: Request
) -> WebhookPolicyResponse:
    tenant = current_tenant_id(request)
    actor = _actor_id(request)
    before = webhook_policy.get_policy(tenant)
    try:
        saved = webhook_policy.set_policy(
            tenant_id=tenant,
            require_https=body.require_https,
            min_tls_version=body.min_tls_version,
            updated_by=actor,
        )
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400,
            detail={
                "code": "webhook_policy_invalid",
                "message": str(e),
            },
        )
    write_event(
        {
            "ts": saved.updated_at,
            "actor": actor,
            "tenant_id": tenant,
            "action": "webhook_policy.update",
            "target": tenant,
            "request_id": getattr(request.state, "request_id", ""),
            "before": before.to_dict(),
            "after": saved.to_dict(),
        }
    )
    return _to_response(request)
