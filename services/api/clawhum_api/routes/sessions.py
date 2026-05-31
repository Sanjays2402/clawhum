"""Active session administration.

Workspace admins can:

* ``GET /sessions`` - list active sessions for the workspace.
* ``DELETE /sessions/{id}`` - revoke a single session (step-up MFA).
* ``POST /sessions/revoke-all`` - revoke every session for an actor or
  the entire workspace (step-up MFA).
* ``GET /sessions/policy`` - read the idle/absolute/PAT TTL caps.
* ``PUT /sessions/policy`` - set those caps (step-up MFA, audited).

Every read and write is tenant-scoped: an admin in tenant A cannot
see, revoke, or alter the policy of tenant B even with a leaked
``X-API-Key``. The cross-tenant isolation is exercised in
``tests/integration/test_sessions.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..auth import require_mfa, require_roles
from ..tenant import current_tenant
from .. import sessions as session_store

router = APIRouter(tags=["sessions"], prefix="/sessions")


class SessionOut(BaseModel):
    id: str
    actor: str
    actor_kind: str
    ip: str
    ua_label: str
    first_seen: float
    last_seen: float
    request_count: int
    revoked: bool
    revoked_at: float
    revoke_reason: str
    is_current: bool = False


class SessionListResponse(BaseModel):
    items: list[SessionOut]
    current_session_id: str = ""


class PolicyOut(BaseModel):
    tenant_id: str
    idle_timeout_minutes: int
    absolute_max_minutes: int
    max_pat_lifetime_minutes: int
    max_pat_age_minutes: int
    updated_at: float


class PolicyIn(BaseModel):
    idle_timeout_minutes: int = Field(ge=0, le=60 * 24 * 365 * 5)
    absolute_max_minutes: int = Field(ge=0, le=60 * 24 * 365 * 5)
    max_pat_lifetime_minutes: int = Field(ge=0, le=60 * 24 * 365 * 5)
    max_pat_age_minutes: int = Field(default=0, ge=0, le=60 * 24 * 365 * 5)


class RevokeAllBody(BaseModel):
    # Either revoke every session for one actor (force-logout for a
    # suspected leaked credential) or the entire workspace (incident
    # response). Exactly one of the two must be provided.
    actor: str | None = Field(default=None, max_length=200)
    all_workspace: bool = False
    reason: str = Field(default="", max_length=120)
    include_self: bool = Field(
        default=False,
        description=(
            "Whether to revoke the caller's own active session. Default "
            "false so the operator is not signed out mid-incident."
        ),
    )


def _to_out(s: session_store.Session, *, current_id: str) -> SessionOut:
    return SessionOut(
        id=s.id,
        actor=s.actor,
        actor_kind=s.actor_kind,
        ip=s.ip,
        ua_label=s.ua_label,
        first_seen=s.first_seen,
        last_seen=s.last_seen,
        request_count=s.request_count,
        revoked=s.revoked,
        revoked_at=s.revoked_at,
        revoke_reason=s.revoke_reason,
        is_current=(s.id == current_id),
    )


def _to_policy_out(p: session_store.SessionPolicy) -> PolicyOut:
    return PolicyOut(
        tenant_id=p.tenant_id,
        idle_timeout_minutes=p.idle_timeout_minutes,
        absolute_max_minutes=p.absolute_max_minutes,
        max_pat_lifetime_minutes=p.max_pat_lifetime_minutes,
        max_pat_age_minutes=p.max_pat_age_minutes,
        updated_at=p.updated_at,
    )


@router.get(
    "",
    response_model=SessionListResponse,
    dependencies=[Depends(require_roles("admin"))],
)
async def list_sessions(
    request: Request,
    include_revoked: bool = False,
    tenant_id: str = Depends(current_tenant),
) -> SessionListResponse:
    rows = session_store.list_sessions(tenant_id, include_revoked=include_revoked)
    current_id = getattr(request.state, "session_id", "") or ""
    return SessionListResponse(
        items=[_to_out(s, current_id=current_id) for s in rows],
        current_session_id=current_id,
    )


@router.get(
    "/policy",
    response_model=PolicyOut,
    dependencies=[Depends(require_roles("admin"))],
)
async def get_policy(tenant_id: str = Depends(current_tenant)) -> PolicyOut:
    return _to_policy_out(session_store.get_policy(tenant_id))


@router.put(
    "/policy",
    response_model=PolicyOut,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def put_policy(body: PolicyIn, tenant_id: str = Depends(current_tenant)) -> PolicyOut:
    try:
        policy = session_store.set_policy(
            tenant_id,
            idle_timeout_minutes=body.idle_timeout_minutes,
            absolute_max_minutes=body.absolute_max_minutes,
            max_pat_lifetime_minutes=body.max_pat_lifetime_minutes,
            max_pat_age_minutes=body.max_pat_age_minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_policy_out(policy)


@router.delete(
    "/{session_id}",
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def delete_session(
    session_id: str,
    request: Request,
    tenant_id: str = Depends(current_tenant),
):
    existing = session_store.get_session(tenant_id, session_id)
    if existing is None:
        # 404 not 403 so admins cannot probe other tenants by id.
        raise HTTPException(status_code=404, detail="session not found")
    current_id = getattr(request.state, "session_id", "") or ""
    if session_id == current_id:
        raise HTTPException(
            status_code=409,
            detail="refusing to revoke caller's own session; use revoke-all with include_self=true",
        )
    session_store.revoke(tenant_id, session_id, reason="manual")
    return Response(status_code=204)


@router.post(
    "/revoke-all",
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def revoke_all(
    body: RevokeAllBody,
    request: Request,
    tenant_id: str = Depends(current_tenant),
):
    if not body.actor and not body.all_workspace:
        raise HTTPException(status_code=400, detail="actor or all_workspace required")
    if body.actor and body.all_workspace:
        raise HTTPException(status_code=400, detail="pass actor or all_workspace, not both")
    current_id = getattr(request.state, "session_id", "") or ""
    if body.all_workspace:
        count = session_store.revoke_all_for_tenant(tenant_id, reason=body.reason or "all-workspace")
    else:
        count = session_store.revoke_all_for_actor(tenant_id, body.actor or "", reason=body.reason or "actor")
    # Bring the caller's own session back to life when include_self is
    # false so the operator who initiated the incident response is not
    # locked out of the very console they used to do it.
    if not body.include_self and current_id:
        session_store.unrevoke_for_tests_or_self(tenant_id, current_id)
    return {"revoked": count}
