"""Per-workspace Support Access Grant administration.

Workspace owners use these routes to grant clawhum support staff
named, scoped, time-boxed access to their tenant. Without an active
grant, any request that carries the ``X-Support-Actor`` header is
rejected 403 at the auth layer; with an active grant, the support
staffer's email and the grant id are stamped into the audit log for
every mutating action they take, giving the customer a defensible
forensic trail.

All mutations require admin role and fresh MFA, matching the rest of
the security sensitive admin surface (IP allowlist, SSO, DPA, security
contacts). Every mutation is tenant-scoped: an admin in tenant A
cannot list, create, or revoke grants in tenant B; the integration
test ``tests/integration/test_support_access.py`` enforces this.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ..auth import require_mfa, require_roles
from ..tenant import current_tenant
from .. import support_access

router = APIRouter(tags=["support-access"], prefix="/support-grants")


# Default TTL surfaced in the UI when an owner does not type one in.
# 24 hours covers the vast majority of routine support sessions while
# still being short enough that customers do not forget the grant is
# open. The hard cap of 7 days lives in support_access.MAX_GRANT_SECONDS.
DEFAULT_TTL_SECONDS = 24 * 3600


class GrantOut(BaseModel):
    id: str
    tenant_id: str
    support_actor: str
    scope: str
    reason: str
    created_at: float
    expires_at: float
    created_by: str
    active: bool
    revoked_at: float | None = None
    revoked_by: str | None = None
    revoke_reason: str | None = None


class GrantListResponse(BaseModel):
    grants: list[GrantOut]
    active_count: int
    max_ttl_seconds: int
    default_ttl_seconds: int
    allowed_scopes: list[str]


class GrantCreate(BaseModel):
    support_actor: str = Field(min_length=3, max_length=254)
    scope: str = Field(default="read", max_length=16)
    reason: str = Field(min_length=1, max_length=500)
    ttl_seconds: int = Field(default=DEFAULT_TTL_SECONDS, ge=1)


class GrantRevoke(BaseModel):
    reason: str = Field(default="", max_length=500)


def _to_out(g: support_access.SupportGrant) -> GrantOut:
    return GrantOut(
        id=g.id,
        tenant_id=g.tenant_id,
        support_actor=g.support_actor,
        scope=g.scope,
        reason=g.reason,
        created_at=g.created_at,
        expires_at=g.expires_at,
        created_by=g.created_by,
        active=g.is_active(),
        revoked_at=g.revoked_at,
        revoked_by=g.revoked_by,
        revoke_reason=g.revoke_reason,
    )


@router.get(
    "",
    response_model=GrantListResponse,
    dependencies=[Depends(require_roles("admin"))],
)
async def list_support_grants(
    tenant_id: str = Depends(current_tenant),
) -> GrantListResponse:
    rows = support_access.list_grants(tenant_id)
    active = [g for g in rows if g.is_active()]
    return GrantListResponse(
        grants=[_to_out(g) for g in rows],
        active_count=len(active),
        max_ttl_seconds=support_access.MAX_GRANT_SECONDS,
        default_ttl_seconds=DEFAULT_TTL_SECONDS,
        allowed_scopes=list(support_access.ALLOWED_SCOPES),
    )


@router.post(
    "",
    response_model=GrantOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def create_support_grant(
    body: GrantCreate,
    request: Request,
    tenant_id: str = Depends(current_tenant),
) -> GrantOut:
    from ..dry_run import is_dry_run, preview
    actor_name = getattr(request.state, "api_key_name", "") or "admin"
    if is_dry_run(request):
        return JSONResponse(preview(
            "support_grant",
            body.support_actor,
            tenant_id=tenant_id,
            scope=body.scope,
            ttl_seconds=body.ttl_seconds,
            reason=body.reason,
        ))
    try:
        grant = support_access.create_grant(
            tenant_id=tenant_id,
            support_actor=body.support_actor,
            scope=body.scope,
            reason=body.reason,
            ttl_seconds=body.ttl_seconds,
            created_by=actor_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_out(grant)


@router.post(
    "/{grant_id}/revoke",
    response_model=GrantOut,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def revoke_support_grant(
    grant_id: str,
    body: GrantRevoke,
    request: Request,
    tenant_id: str = Depends(current_tenant),
) -> GrantOut:
    from ..dry_run import is_dry_run, preview
    existing = support_access.get_grant(tenant_id, grant_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="support grant not found")
    if is_dry_run(request):
        return JSONResponse(preview(
            "support_grant_revoke",
            grant_id,
            tenant_id=tenant_id,
            support_actor=existing.support_actor,
            scope=existing.scope,
        ))
    actor_name = getattr(request.state, "api_key_name", "") or "admin"
    revoked = support_access.revoke_grant(
        tenant_id=tenant_id,
        grant_id=grant_id,
        revoked_by=actor_name,
        reason=body.reason,
    )
    if revoked is None:
        raise HTTPException(status_code=404, detail="support grant not found")
    return _to_out(revoked)


@router.delete(
    "/{grant_id}",
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def delete_support_grant(
    grant_id: str,
    request: Request,
    tenant_id: str = Depends(current_tenant),
):
    """Alias for ``POST /support-grants/{grant_id}/revoke`` so SDKs that
    treat DELETE as the natural revoke verb get the same effect.

    Returns 204 on success. Dry-run mode returns a JSON preview so a
    UI can show "this would revoke the grant" before committing.
    """
    from ..dry_run import is_dry_run, preview
    existing = support_access.get_grant(tenant_id, grant_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="support grant not found")
    if is_dry_run(request):
        return JSONResponse(preview(
            "support_grant_revoke",
            grant_id,
            tenant_id=tenant_id,
            support_actor=existing.support_actor,
            scope=existing.scope,
        ))
    actor_name = getattr(request.state, "api_key_name", "") or "admin"
    support_access.revoke_grant(
        tenant_id=tenant_id,
        grant_id=grant_id,
        revoked_by=actor_name,
        reason="deleted via DELETE",
    )
    return Response(status_code=204)
