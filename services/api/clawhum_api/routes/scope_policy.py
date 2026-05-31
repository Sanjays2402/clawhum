"""Per-workspace PAT scope policy administration.

Admin-only set/clear of the maximum scope set the workspace allows
on PATs. Read is open to readers so the dashboard can show the
current pin. Tenant scoped on every call.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from .. import scope_policy
from ..api_keys import SCOPES, normalise_scopes
from ..audit import write_event
from ..auth import require_admin_with_mfa, require_roles
from ..tenant import current_tenant_id

router = APIRouter(tags=["scope-policy"], prefix="/scope-policy")


class ScopePolicyResponse(BaseModel):
    enforcing: bool
    scopes: list[str]
    available_scopes: list[str]
    updated_at: float = 0.0
    updated_by: str = ""


class ScopePolicyUpdate(BaseModel):
    scopes: list[str] = Field(
        default_factory=list,
        description=(
            "Maximum scope set this workspace may ever mint on a PAT. "
            "Pass an empty list to clear the policy and return to "
            "\"no restriction\". Unknown scopes are silently dropped."
        ),
    )


def _actor_id(request: Request) -> str:
    return (
        getattr(request.state, "api_key_name", "")
        or getattr(request.state, "pat_id", "")
        or "unknown"
    )


def _to_response(request: Request) -> ScopePolicyResponse:
    tenant = current_tenant_id(request)
    pol = scope_policy.get_policy(tenant)
    return ScopePolicyResponse(
        enforcing=bool(pol and pol.scopes),
        scopes=sorted(pol.scopes) if pol else [],
        available_scopes=sorted(SCOPES),
        updated_at=pol.updated_at if pol else 0.0,
        updated_by=pol.updated_by if pol else "",
    )


@router.get(
    "",
    response_model=ScopePolicyResponse,
    dependencies=[Depends(require_roles("reader"))],
)
async def get_scope_policy(request: Request) -> ScopePolicyResponse:
    return _to_response(request)


@router.put(
    "",
    response_model=ScopePolicyResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def set_scope_policy(
    body: ScopePolicyUpdate, request: Request
) -> ScopePolicyResponse:
    tenant = current_tenant_id(request)
    actor = _actor_id(request)
    before = scope_policy.get_policy(tenant)
    saved = scope_policy.set_policy(
        tenant_id=tenant,
        scopes=normalise_scopes(body.scopes),
        updated_by=actor,
    )
    write_event(
        {
            "ts": saved.updated_at,
            "actor": actor,
            "tenant_id": tenant,
            "action": "scope_policy.update",
            "target": tenant,
            "request_id": getattr(request.state, "request_id", ""),
            "before": before.to_dict() if before else {"scopes": []},
            "after": saved.to_dict(),
        }
    )
    return _to_response(request)
