"""Per-workspace allowed authentication methods administration.

Admin-only set/clear of which credential classes the workspace
accepts (``env_key``, ``pat``, ``scim``). Read is open to readers so
the dashboard can render the current pin. Tenant scoped on every call.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import auth_methods_policy
from ..audit import write_event
from ..auth import require_admin_with_mfa, require_roles
from ..tenant import current_tenant_id

router = APIRouter(tags=["auth-methods-policy"], prefix="/auth-methods-policy")


class PolicyResponse(BaseModel):
    enforcing: bool
    methods: list[str]
    available_methods: list[str]
    effective_methods: list[str]
    updated_at: float = 0.0
    updated_by: str = ""


class PolicyUpdate(BaseModel):
    methods: list[str] = Field(
        default_factory=list,
        description=(
            "Credential classes this workspace accepts. Must contain "
            "at least one of 'env_key', 'pat', 'scim'. Unknown values "
            "are silently dropped."
        ),
    )


def _actor_id(request: Request) -> str:
    return (
        getattr(request.state, "api_key_name", "")
        or getattr(request.state, "pat_id", "")
        or "unknown"
    )


def _to_response(tenant: str) -> PolicyResponse:
    pol = auth_methods_policy.get_policy(tenant)
    eff = sorted(auth_methods_policy.allowed_methods(tenant))
    return PolicyResponse(
        enforcing=bool(pol and pol.methods),
        methods=sorted(pol.methods) if pol else [],
        available_methods=sorted(auth_methods_policy.METHODS),
        effective_methods=eff,
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
    # Validate at the HTTP edge: an empty methods set would lock the
    # workspace out of the API, so we refuse and let the admin pass an
    # explicit ['env_key','pat','scim'] to reset.
    cleaned = [m for m in (body.methods or []) if m in auth_methods_policy.METHODS]
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "methods must contain at least one of "
                f"{sorted(auth_methods_policy.METHODS)}"
            ),
        )
    before = auth_methods_policy.get_policy(tenant)
    saved = auth_methods_policy.set_policy(
        tenant_id=tenant,
        methods=cleaned,
        updated_by=actor,
    )
    write_event(
        {
            "ts": saved.updated_at,
            "actor": actor,
            "tenant_id": tenant,
            "action": "auth_methods_policy.update",
            "target": tenant,
            "request_id": getattr(request.state, "request_id", ""),
            "before": before.to_dict() if before else {"methods": []},
            "after": saved.to_dict(),
        }
    )
    return _to_response(tenant)
