"""Self serve personal access tokens.

A logged-in tenant (any caller with a valid ``writer`` role on their
current key) can list, mint, and revoke PATs scoped to their own
tenant. The freshly minted secret is returned ONCE in the create
response; afterwards only a four-character hint is exposed. PATs
authenticate against the same ``X-API-Key`` header that the rest of
the API uses, so existing curl snippets and SDKs work unchanged.

Routes are mounted both at the unversioned path (for the web UI) and
under ``/v1`` for stable customer integrations.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import pat_store
from .. import pat_ip_history
from ..api_keys import ROLES, SCOPES, normalise_scopes, scopes_allowed_for_roles
from ..auth import require_mfa, require_roles, require_scopes
from ..tenant import current_tenant_id
from clawhum_core.settings import get_settings

router = APIRouter(tags=["keys"])


class CreateKeyBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    roles: list[str] | None = None  # defaults to caller's roles intersected with ROLES
    rpm: int | None = Field(default=0, ge=0, le=100_000)
    expires_in_days: int | None = Field(
        default=None,
        ge=0,
        le=3650,
        description=(
            "Token lifetime in days. Omit to use the workspace default. "
            "0 requests a non-expiring token, which the server will "
            "clamp to the configured max when a cap is set."
        ),
    )
    scopes: list[str] | None = Field(
        default=None,
        description=(
            "Fine-grained scopes for least-privilege machine tokens. "
            "Omit or pass an empty list to grant every scope this PAT's "
            "roles permit. Unknown scopes are silently dropped; scopes "
            "above the caller's role are clamped server-side."
        ),
    )
    ip_cidrs: list[str] | None = Field(
        default=None,
        max_length=64,
        description=(
            "Optional list of CIDR ranges (IPv4 or IPv6) that this "
            "token may be used from. Empty or omitted means no IP "
            "restriction. Requests from any other client IP are "
            "rejected with HTTP 403. Use this to pin CI tokens to a "
            "build farm or office VPN range."
        ),
    )


class KeyView(BaseModel):
    id: str
    name: str
    roles: list[str]
    rpm: int
    created_at: float
    last_used_at: float
    last_used_ip: str = ""
    last_used_ua: str = ""
    secret_hint: str
    expires_at: float
    expired: bool
    scopes: list[str]
    effective_scopes: list[str]
    prior_secret_hint: str = ""
    prior_secret_expires_at: float = 0.0
    rotation_active: bool = False
    ip_cidrs: list[str] = Field(default_factory=list)
    # Force-rotation policy state (populated from active workspace
    # SessionPolicy on read). 0 / None mean the policy is unset.
    max_age_minutes: int = 0
    age_seconds_remaining: int | None = None
    aged_out: bool = False
    # Idle / unused credential revocation policy state.
    max_idle_minutes: int = 0
    idle_seconds_remaining: int | None = None
    idle_revoked: bool = False


class KeyCreateResponse(KeyView):
    secret: str  # plaintext, shown ONCE


class KeyPolicyResponse(BaseModel):
    max_ttl_days: int
    default_ttl_days: int
    available_scopes: list[str]
    allowed_scopes: list[str]
    workspace_scope_policy: list[str] = Field(default_factory=list)
    workspace_scope_policy_enforcing: bool = False


@router.get(
    "/keys",
    response_model=list[KeyView],
    dependencies=[Depends(require_roles("writer"))],
)
async def list_keys(request: Request) -> list[dict[str, Any]]:
    tenant = current_tenant_id(request)
    return [pat_store.public_view(p) for p in pat_store.live_for_tenant(tenant)]


@router.get(
    "/keys/policy",
    response_model=KeyPolicyResponse,
    dependencies=[Depends(require_roles("writer"))],
)
async def keys_policy(request: Request) -> dict[str, Any]:
    """Expose the workspace PAT lifetime policy to the UI.

    The mint form reads this to render the TTL picker so it never
    offers a value the server will reject. The scope arrays let the
    UI render checkboxes that already reflect the caller's role
    ceiling, so a reader is not offered ``write:library`` to begin
    with.
    """
    s = get_settings()
    caller_roles: frozenset[str] = getattr(request.state, "api_key_roles", frozenset())
    from .. import scope_policy as _scope_policy
    tenant = current_tenant_id(request)
    role_allowed = scopes_allowed_for_roles(caller_roles)
    workspace_allowed = _scope_policy.allowed_scopes(tenant)
    effective = role_allowed & workspace_allowed
    return {
        "max_ttl_days": int(s.pat_max_ttl_days or 0),
        "default_ttl_days": int(s.pat_default_ttl_days or 0),
        "available_scopes": sorted(SCOPES),
        "allowed_scopes": sorted(effective),
        "workspace_scope_policy": sorted(workspace_allowed) if _scope_policy.has_policy(tenant) else [],
        "workspace_scope_policy_enforcing": _scope_policy.has_policy(tenant),
    }


@router.post(
    "/keys",
    response_model=KeyCreateResponse,
    dependencies=[Depends(require_roles("writer"))],
)
async def create_key(body: CreateKeyBody, request: Request) -> dict[str, Any]:
    tenant = current_tenant_id(request)
    caller_roles: frozenset[str] = getattr(request.state, "api_key_roles", frozenset())
    # Default to whatever the caller already has, intersected with the
    # canonical role set. This makes the common case (writer mints a
    # writer token) one click in the UI.
    requested = (
        frozenset(r.lower() for r in (body.roles or []) if r) or caller_roles
    )
    requested = requested & ROLES
    # A caller cannot mint a token wider than themselves. Admin can
    # mint anything they already hold.
    if "admin" not in caller_roles:
        requested = requested & caller_roles
    if not requested:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no valid roles requested",
        )
    try:
        pat, secret = pat_store.create(
            tenant_id=tenant,
            name=body.name,
            roles=requested,
            rpm=body.rpm or 0,
            expires_in_days=body.expires_in_days,
            scopes=normalise_scopes(body.scopes or []),
            ip_cidrs=body.ip_cidrs or [],
        )
    except ValueError as exc:
        # scope_policy.ScopeNotAllowedError is a ValueError subclass;
        # surface its denied set so the UI can show which scopes the
        # workspace policy forbids without grepping the message.
        from .. import scope_policy as _scope_policy
        if isinstance(exc, _scope_policy.ScopeNotAllowedError):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "scope_not_allowed",
                    "message": str(exc),
                    "denied": sorted(exc.denied),
                },
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid ip_cidrs: {exc}",
        )
    view = pat_store.public_view(pat)
    view["secret"] = secret
    return view


class RevokeAllBody(BaseModel):
    include_self: bool = Field(
        default=False,
        description=(
            "When the caller authenticated with a personal access token, "
            "that token is preserved by default so the operator is not "
            "signed out mid-incident. Set true to revoke it too."
        ),
    )


class RevokeAllResponse(BaseModel):
    ok: bool
    revoked: list[str]
    preserved: str | None = None


@router.post(
    "/keys/revoke-all",
    response_model=RevokeAllResponse,
    dependencies=[Depends(require_roles("writer")), Depends(require_mfa())],
)
async def revoke_all_keys(
    body: RevokeAllBody,
    request: Request,
) -> dict[str, Any]:
    """Incident-response: invalidate every PAT in the workspace.

    Returns the ids that were revoked. By default the caller's own PAT
    (if any) is preserved so a single operator can recover from a
    suspected credential compromise without locking themselves out;
    pass ``include_self: true`` to revoke it too.
    """
    from ..dry_run import is_dry_run, preview
    tenant = current_tenant_id(request)
    self_pat_id: str | None = getattr(request.state, "pat_id", None)
    except_id = None if body.include_self else self_pat_id
    live = pat_store.live_for_tenant(tenant)
    targets = [p.id for p in live if except_id is None or p.id != except_id]
    if is_dry_run(request):
        return preview(
            "api_key_bulk",
            "all",
            tenant_id=tenant,
            count=len(targets),
            target_ids=targets,
            preserved=except_id,
        )
    revoked = pat_store.revoke_all_for_tenant(
        tenant_id=tenant,
        except_pat_id=except_id,
    )
    return {"ok": True, "revoked": revoked, "preserved": except_id}


@router.delete(
    "/keys/{key_id}",
    dependencies=[Depends(require_roles("writer")), Depends(require_mfa())],
)
async def revoke_key(key_id: str, request: Request) -> dict[str, Any]:
    tenant = current_tenant_id(request)
    from ..dry_run import is_dry_run, preview
    if is_dry_run(request):
        existing = next((p for p in pat_store.live_for_tenant(tenant) if p.id == key_id), None)
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="key not found")
        return preview("api_key", key_id, tenant_id=tenant,
                       name=existing.name, roles=sorted(existing.roles))
    ok = pat_store.revoke(tenant_id=tenant, pat_id=key_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="key not found")
    return {"ok": True, "id": key_id}


class RotateKeyBody(BaseModel):
    grace_minutes: int | None = Field(
        default=None,
        ge=0,
        le=10_080,
        description=(
            "Minutes the previous secret keeps authenticating after "
            "rotation. Omit to use the workspace default. 0 revokes "
            "the old secret immediately. The server clamps to the "
            "operator cap (pat_rotation_max_grace_minutes)."
        ),
    )


class RotateKeyResponse(KeyView):
    secret: str  # plaintext, shown ONCE


@router.post(
    "/keys/{key_id}/rotate",
    response_model=RotateKeyResponse,
    dependencies=[Depends(require_roles("writer")), Depends(require_mfa())],
)
async def rotate_key(
    key_id: str,
    body: RotateKeyBody,
    request: Request,
) -> dict[str, Any]:
    """Mint a fresh secret for an existing PAT, keeping the id stable.

    The new secret is returned exactly once in the response. The old
    secret keeps authenticating for ``grace_minutes`` so a rolling
    deploy can swap credentials without dropping requests. Pass 0 to
    revoke the old secret immediately (use this when you suspect a
    leak). The server clamps the grace window to the operator-defined
    ceiling so a workspace owner cannot extend it indefinitely.
    """
    from ..dry_run import is_dry_run, preview
    tenant = current_tenant_id(request)
    existing = next(
        (p for p in pat_store.live_for_tenant(tenant) if p.id == key_id),
        None,
    )
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="key not found"
        )
    if is_dry_run(request):
        return preview(
            "api_key_rotation",
            key_id,
            tenant_id=tenant,
            name=existing.name,
            grace_minutes=body.grace_minutes,
        )
    result = pat_store.rotate(
        tenant_id=tenant,
        pat_id=key_id,
        grace_minutes=body.grace_minutes,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="key not found"
        )
    pat, secret = result
    view = pat_store.public_view(pat)
    view["secret"] = secret
    return view


class IpAllowlistBody(BaseModel):
    cidrs: list[str] = Field(
        default_factory=list,
        max_length=64,
        description=(
            "Replacement list of CIDR ranges the token may be used from. "
            "Pass an empty list to clear the restriction. IPv4 and IPv6 "
            "are both accepted; host addresses are normalised to /32 or "
            "/128. Invalid input returns 400 with the offending value."
        ),
    )


class IpAllowlistResponse(BaseModel):
    ok: bool
    id: str
    ip_cidrs: list[str]


@router.put(
    "/keys/{key_id}/ip-allowlist",
    response_model=IpAllowlistResponse,
    dependencies=[Depends(require_roles("writer")), Depends(require_mfa())],
)
async def set_key_ip_allowlist(
    key_id: str,
    body: IpAllowlistBody,
    request: Request,
) -> dict[str, Any]:
    """Pin a PAT to a list of source IP ranges.

    The empty list clears the restriction (token usable from any IP).
    Step-up MFA is required because tightening or loosening the IP
    fence is destructive: a misconfigured CIDR can lock a CI pipeline
    out, and a loosened fence widens the blast radius if the token
    leaks. The mutation is captured by the audit middleware so a
    forensic review can reconstruct who changed what when.
    """
    from ..dry_run import is_dry_run, preview
    tenant = current_tenant_id(request)
    existing = next(
        (p for p in pat_store.live_for_tenant(tenant) if p.id == key_id),
        None,
    )
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="key not found"
        )
    if is_dry_run(request):
        return preview(
            "api_key_ip_allowlist",
            key_id,
            tenant_id=tenant,
            name=existing.name,
            previous=sorted(existing.ip_cidrs),
            requested=list(body.cidrs),
        )
    try:
        updated = pat_store.set_ip_cidrs(
            tenant_id=tenant, pat_id=key_id, cidrs=body.cidrs
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid ip_cidrs: {exc}",
        )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="key not found"
        )
    return {"ok": True, "id": updated.id, "ip_cidrs": sorted(updated.ip_cidrs)}



class IpHistoryEntryView(BaseModel):
    ip: str
    first_seen: float
    last_seen: float
    count: int
    last_ua: str = ""


class IpHistoryResponse(BaseModel):
    id: str
    name: str
    distinct_ips: int
    truncated: bool = False
    items: list[IpHistoryEntryView]


_HISTORY_MAX_ITEMS = 100


@router.get(
    "/keys/{key_id}/ip-history",
    response_model=IpHistoryResponse,
    dependencies=[Depends(require_roles("admin"))],
)
async def key_ip_history(key_id: str, request: Request) -> dict[str, Any]:
    """Forensic timeline of every source IP that has used this token.

    Admin only. Cross tenant lookups return 404 (not 403) so a probing
    attacker cannot enumerate token ids across workspaces. Each entry
    records first_seen, last_seen, total successful auth count, and a
    truncated user-agent from the most recent hit. Use this to triage
    a suspected credential leak: an unexpected ip with a high count is
    the signal to rotate or revoke.
    """
    tenant = current_tenant_id(request)
    existing = next(
        (p for p in pat_store.live_for_tenant(tenant) if p.id == key_id),
        None,
    )
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="key not found"
        )
    rows = pat_ip_history.list_for_pat(tenant, key_id)
    truncated = len(rows) > _HISTORY_MAX_ITEMS
    rows = rows[:_HISTORY_MAX_ITEMS]
    return {
        "id": existing.id,
        "name": existing.name,
        "distinct_ips": len(rows),
        "truncated": truncated,
        "items": [
            {
                "ip": r.ip,
                "first_seen": r.first_seen,
                "last_seen": r.last_seen,
                "count": r.count,
                "last_ua": r.last_ua,
            }
            for r in rows
        ],
    }
