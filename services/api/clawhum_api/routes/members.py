"""Workspace member management.

Endpoints:
- GET    /members              roster + invite list for the caller's workspace
- POST   /members/invite       mint a one shot invite token (admin + MFA)
- POST   /members/accept       accept an invite by token (public, token gated)
- PATCH  /members/{id}         change a member's role (admin + MFA)
- DELETE /members/{id}         revoke a member or pending invite (admin + MFA)

The accept endpoint is intentionally public so a recipient who is not
yet authenticated against the workspace can claim their seat. The
token carries all the entropy; an attacker without it learns nothing.

All mutations flow through the global AuditLogMiddleware. Tenant
scoping is enforced inside member_store; this layer also re-derives
the tenant from the request so the admin cannot pass another tenant
id in a URL and mutate someone else's roster.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import member_store
from ..api_keys import ROLES, ANON_TENANT_ID, DEV_TENANT_ID
from ..auth import require_admin_with_mfa, require_roles
from ..tenant import current_tenant_id

router = APIRouter(tags=["members"])


class MemberView(BaseModel):
    id: str
    email: str
    role: str
    status: str
    invited_by: str
    invited_at: float
    accepted_at: float
    invite_expires_at: float


class MemberListResponse(BaseModel):
    members: list[MemberView]
    counts: dict[str, int]


class InviteBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: str = Field(min_length=1, max_length=16)
    ttl_hours: int | None = Field(default=None, ge=0, le=24 * 365)


class InviteResponse(MemberView):
    invite_token: str  # plaintext, shown ONCE


class AcceptBody(BaseModel):
    token: str = Field(min_length=8, max_length=128)


class UpdateRoleBody(BaseModel):
    role: str = Field(min_length=1, max_length=16)


def _guard_tenant(tenant_id: str) -> str:
    """Refuse to mutate the dev or anonymous tenants from the API.

    Both are sentinels used when auth is open or the caller had no key.
    Pretending they have members would let any drive by request mint
    invites; refusing keeps the model honest.
    """
    if not tenant_id or tenant_id in (ANON_TENANT_ID, DEV_TENANT_ID):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="member management requires a workspace-scoped api key",
        )
    return tenant_id


@router.get(
    "/members",
    response_model=MemberListResponse,
    dependencies=[Depends(require_roles("reader"))],
)
async def list_members(request: Request) -> dict[str, Any]:
    tenant = _guard_tenant(current_tenant_id(request))
    rows = member_store.list_for_tenant(tenant)
    return {
        "members": [m.public_dict() for m in rows],
        "counts": member_store.count_for_tenant(tenant),
    }


@router.post(
    "/members/invite",
    response_model=InviteResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def invite_member(request: Request, body: InviteBody) -> dict[str, Any]:
    tenant = _guard_tenant(current_tenant_id(request))
    actor = getattr(request.state, "api_key_name", "unknown")
    try:
        member, token = member_store.invite(
            tenant_id=tenant,
            email=body.email,
            role=body.role,
            invited_by=actor,
            ttl_hours=body.ttl_hours,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    payload = member.public_dict()
    payload["invite_token"] = token
    return payload


@router.post(
    "/members/accept",
    response_model=MemberView,
    status_code=status.HTTP_200_OK,
)
async def accept_invite(body: AcceptBody) -> dict[str, Any]:
    try:
        member = member_store.accept(body.token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return member.public_dict()


@router.patch(
    "/members/{member_id}",
    response_model=MemberView,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def update_member_role(
    request: Request, member_id: str, body: UpdateRoleBody,
) -> dict[str, Any]:
    tenant = _guard_tenant(current_tenant_id(request))
    try:
        member = member_store.update_role(member_id, role=body.role, tenant_id=tenant)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return member.public_dict()


@router.delete(
    "/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def revoke_member(request: Request, member_id: str) -> None:
    tenant = _guard_tenant(current_tenant_id(request))
    try:
        member_store.revoke(member_id, tenant_id=tenant)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return None
