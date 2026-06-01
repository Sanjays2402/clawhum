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
from .. import pat_trusted_devices
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
    path_prefixes: list[str] | None = Field(
        default=None,
        max_length=32,
        description=(
            "Optional list of URL path prefixes the token may reach. "
            "Empty or omitted means no path restriction. Each entry "
            "must start with '/'; a request matches when its path "
            "equals the prefix or is followed by '/'. Use this for "
            "least-privilege: pin a CI token to '/match' so a leak "
            "cannot also drain '/library' or '/exports'."
        ),
    )
    http_methods: list[str] | None = Field(
        default=None,
        max_length=7,
        description=(
            "Optional list of HTTP methods the token may use. Empty "
            "or omitted means no method restriction. Allowed verbs: "
            "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS. Use "
            "['GET','HEAD'] to mint a true read-only token whose leak "
            "cannot mutate state regardless of scopes. HEAD is always "
            "implicitly allowed when GET is, so monitoring probes do "
            "not break."
        ),
    )
    usage_windows: list[str] | None = Field(
        default=None,
        max_length=16,
        description=(
            "Optional list of UTC time windows during which this "
            "token may authenticate. Each entry is "
            "'<days>:HH:MM-HH:MM' where days is mon..sun, 'all', or "
            "a range like 'mon-fri'. Empty / omitted means no time "
            "restriction. Use this to shrink the leak blast-radius "
            "for tokens that only run during business hours or a "
            "nightly job; outside the window the API returns 403."
        ),
    )
    owner_email: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "Optional contact email for the human who owns this token "
            "(e.g. the on-call engineer for the CI job using it). "
            "Empty / omitted is allowed; when set, the value must "
            "look like name@example.com. Surfaced in the workspace key "
            "inventory so a SOC2 reviewer can answer who to page if "
            "the credential is leaked."
        ),
    )
    description: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Optional free-text purpose / runbook note for the token "
            "(e.g. 'CI deploy bot, owned by platform-eng'). Empty / "
            "omitted is allowed; non-empty input is whitespace-collapsed, "
            "control-character stripped, and capped at 200 chars. "
            "Surfaced in the credential inventory so SOC2 / ISO 27001 "
            "reviewers can answer 'what does this token do?' during "
            "an access review without grepping ticket history."
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
    path_prefixes: list[str] = Field(default_factory=list)
    require_device_approval: bool = False
    http_methods: list[str] = Field(default_factory=list)
    usage_windows: list[str] = Field(default_factory=list)
    owner_email: str = ""
    description: str = ""
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
    # Per-workspace allowed-auth-methods policy. When a workspace has
    # disabled the 'pat' credential class, block mint at the door with
    # a deterministic 403 so the UI can surface the runbook ("contact
    # an owner to re-enable PATs or use a SCIM-provisioned account").
    from .. import auth_methods_policy as _amp
    if not _amp.is_allowed(tenant, "pat"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "pat_minting_disabled: personal access tokens are "
                "disabled for this workspace by the auth methods policy"
            ),
            headers={"X-Auth-Method-Disabled": "pat"},
        )
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
            path_prefixes=body.path_prefixes or [],
            http_methods=body.http_methods or [],
            usage_windows=body.usage_windows or [],
            owner_email=body.owner_email,
            description=body.description,
        )
    except ValueError as exc:
        # scope_policy.ScopeNotAllowedError is a ValueError subclass;
        # surface its denied set so the UI can show which scopes the
        # workspace policy forbids without grepping the message.
        from .. import scope_policy as _scope_policy
        from .. import pat_concurrency as _pat_concurrency
        from .. import pat_min_requirements as _pat_min_requirements
        if isinstance(exc, _scope_policy.ScopeNotAllowedError):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "scope_not_allowed",
                    "message": str(exc),
                    "denied": sorted(exc.denied),
                },
            )
        if isinstance(exc, _pat_concurrency.PatConcurrencyExceeded):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "pat_concurrency_exceeded",
                    "message": str(exc),
                    "live": exc.live,
                    "max_active": exc.max_active,
                },
                headers={"Retry-After": "0"},
            )
        if isinstance(exc, _pat_min_requirements.PatMinRequirementsViolation):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "pat_min_requirements_violation",
                    "message": str(exc),
                    "violations": list(exc.violations),
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
    # Tear down the per-PAT device approval list too so a future
    # token reusing the same id (extremely unlikely with random ids,
    # but cheap to be defensive about) does not inherit stale trust.
    try:
        pat_trusted_devices.revoke_all_for_pat(tenant_id=tenant, pat_id=key_id)
    except Exception:
        pass
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


class PathPrefixesBody(BaseModel):
    path_prefixes: list[str] = Field(
        default_factory=list,
        max_length=32,
        description=(
            "Replacement list of URL path prefixes the token may "
            "reach. Pass an empty list to clear the restriction. "
            "Each entry must start with '/'. Invalid input returns "
            "400 with the offending value."
        ),
    )


class PathPrefixesResponse(BaseModel):
    ok: bool
    id: str
    path_prefixes: list[str]


@router.put(
    "/keys/{key_id}/path-allowlist",
    response_model=PathPrefixesResponse,
    dependencies=[Depends(require_roles("writer")), Depends(require_mfa())],
)
async def set_key_path_allowlist(
    key_id: str,
    body: PathPrefixesBody,
    request: Request,
) -> dict[str, Any]:
    """Pin a PAT to a list of URL path prefixes.

    The empty list clears the restriction. Step-up MFA is required
    because tightening or loosening the path fence is destructive: a
    misconfigured prefix can lock a CI pipeline out, and a loosened
    fence widens the blast radius if the token leaks.
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
            "api_key_path_allowlist",
            key_id,
            tenant_id=tenant,
            name=existing.name,
            previous=sorted(existing.path_prefixes),
            requested=list(body.path_prefixes),
        )
    try:
        updated = pat_store.set_path_prefixes(
            tenant_id=tenant, pat_id=key_id, prefixes=body.path_prefixes
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid path_prefixes: {exc}",
        )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="key not found"
        )
    return {
        "ok": True,
        "id": updated.id,
        "path_prefixes": sorted(updated.path_prefixes),
    }


class MethodAllowlistBody(BaseModel):
    http_methods: list[str] = Field(
        default_factory=list,
        max_length=7,
        description=(
            "Replacement list of HTTP methods the token may use. "
            "Pass an empty list to clear the restriction (any verb). "
            "Allowed values: GET, HEAD, POST, PUT, PATCH, DELETE, "
            "OPTIONS. Unknown verbs return 400 with the offending value."
        ),
    )


class MethodAllowlistResponse(BaseModel):
    ok: bool
    id: str
    http_methods: list[str]


@router.put(
    "/keys/{key_id}/method-allowlist",
    response_model=MethodAllowlistResponse,
    dependencies=[Depends(require_roles("writer")), Depends(require_mfa())],
)
async def set_key_method_allowlist(
    key_id: str,
    body: MethodAllowlistBody,
    request: Request,
) -> dict[str, Any]:
    """Pin a PAT to a list of HTTP methods.

    The empty list clears the restriction. Step-up MFA is required
    because tightening or loosening the method fence is destructive:
    flipping a read-only token to read-write widens the blast radius
    on a leak, and pinning to a too-narrow set can lock a CI
    pipeline out mid-deploy.
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
            "api_key_method_allowlist",
            key_id,
            tenant_id=tenant,
            name=existing.name,
            previous=sorted(existing.http_methods),
            requested=list(body.http_methods),
        )
    try:
        updated = pat_store.set_http_methods(
            tenant_id=tenant, pat_id=key_id, methods=body.http_methods
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid http_methods: {exc}",
        )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="key not found"
        )
    return {
        "ok": True,
        "id": updated.id,
        "http_methods": sorted(updated.http_methods),
    }


class UsageWindowBody(BaseModel):
    usage_windows: list[str] = Field(
        default_factory=list,
        max_length=16,
        description=(
            "Replacement list of UTC usage windows for the token. "
            "Each entry is '<days>:HH:MM-HH:MM' where days is one of "
            "mon..sun, 'all', or a contiguous range like 'mon-fri'. "
            "Pass an empty list to clear the restriction. Bad input "
            "returns 400 with the parser message so the UI can show "
            "the offending entry inline."
        ),
    )


class UsageWindowResponse(BaseModel):
    ok: bool
    id: str
    usage_windows: list[str]


@router.put(
    "/keys/{key_id}/usage-window",
    response_model=UsageWindowResponse,
    dependencies=[Depends(require_roles("writer")), Depends(require_mfa())],
)
async def set_key_usage_window(
    key_id: str,
    body: UsageWindowBody,
    request: Request,
) -> dict[str, Any]:
    """Pin a PAT to a list of UTC usage windows.

    Empty list clears the restriction. Step-up MFA is required
    because widening the window weakens the leak fence and shrinking
    it can lock a running CI job out mid-deploy; both directions are
    destructive enough to warrant the same gate as the other PAT
    fences. Dry-run preview returns the previous and requested
    windows so an operator can confirm intent before flipping.
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
            "api_key_usage_window",
            key_id,
            tenant_id=tenant,
            name=existing.name,
            previous=sorted(existing.usage_windows),
            requested=list(body.usage_windows),
        )
    try:
        updated = pat_store.set_usage_windows(
            tenant_id=tenant, pat_id=key_id, windows=body.usage_windows
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid usage_windows: {exc}",
        )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="key not found"
        )
    return {
        "ok": True,
        "id": updated.id,
        "usage_windows": sorted(updated.usage_windows),
    }


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


# ---------------------------------------------------------------------------
# Trusted devices: per-PAT device fingerprint approval list.
#
# When ``require_device_approval`` is on for a PAT, the auth layer
# rejects any request whose device fingerprint (resolved client IP
# prefix + coarse User-Agent family) is not on the approved list.
# Unknown devices are auto-recorded as ``pending`` and surfaced here
# so the workspace owner can approve or revoke them. This is a real
# enterprise control: even if a PAT secret leaks, the attacker still
# has to use it from a device the owner has approved out of band.
# ---------------------------------------------------------------------------


class DeviceApprovalBody(BaseModel):
    required: bool = Field(
        description=(
            "When true, only devices on this PAT's approved list may "
            "use the token; unknown devices receive 403 and are added "
            "to the pending queue. Turning this on does NOT auto-trust "
            "any currently-in-use device, so an admin has to take an "
            "explicit approve action before the next request can pass."
        )
    )


class DeviceApprovalResponse(BaseModel):
    ok: bool
    id: str
    require_device_approval: bool
    has_approved_device: bool


@router.put(
    "/keys/{key_id}/device-approval",
    response_model=DeviceApprovalResponse,
    dependencies=[Depends(require_roles("writer")), Depends(require_mfa())],
)
async def set_key_device_approval(
    key_id: str,
    body: DeviceApprovalBody,
    request: Request,
) -> dict[str, Any]:
    """Toggle per-PAT trusted-device strict mode.

    Step-up MFA is required: tightening the bit can lock a CI box
    out until the device is approved, and loosening it widens the
    blast radius of a leaked secret. The mutation is captured by
    the audit middleware automatically.
    """
    from ..dry_run import is_dry_run, preview
    tenant = current_tenant_id(request)
    existing = next(
        (p for p in pat_store.live_for_tenant(tenant) if p.id == key_id), None
    )
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="key not found"
        )
    if is_dry_run(request):
        return preview(
            "api_key_device_approval",
            key_id,
            tenant_id=tenant,
            name=existing.name,
            previous=existing.require_device_approval,
            requested=bool(body.required),
        )
    updated = pat_store.set_require_device_approval(
        tenant_id=tenant, pat_id=key_id, required=bool(body.required)
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="key not found"
        )
    return {
        "ok": True,
        "id": updated.id,
        "require_device_approval": updated.require_device_approval,
        "has_approved_device": pat_trusted_devices.has_approved_device(
            tenant, updated.id
        ),
    }


class DeviceView(BaseModel):
    fingerprint: str
    status: str
    label: str = ""
    first_seen: float
    last_seen: float
    count: int
    last_ua: str = ""
    last_ip: str = ""


class DevicesResponse(BaseModel):
    id: str
    name: str
    require_device_approval: bool
    devices: list[DeviceView]


@router.get(
    "/keys/{key_id}/devices",
    response_model=DevicesResponse,
    dependencies=[Depends(require_roles("admin"))],
)
async def list_key_devices(key_id: str, request: Request) -> dict[str, Any]:
    """List approved + pending devices for a PAT. Admin only.

    Cross tenant lookups return 404 (not 403) so a probing attacker
    cannot enumerate token ids across workspaces. Pending devices are
    sorted before approved ones so the queue that needs action is
    visible first.
    """
    tenant = current_tenant_id(request)
    existing = next(
        (p for p in pat_store.live_for_tenant(tenant) if p.id == key_id), None
    )
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="key not found"
        )
    rows = pat_trusted_devices.list_for_pat(tenant, key_id)
    return {
        "id": existing.id,
        "name": existing.name,
        "require_device_approval": existing.require_device_approval,
        "devices": [
            {
                "fingerprint": d.fingerprint,
                "status": d.status,
                "label": d.label,
                "first_seen": d.first_seen,
                "last_seen": d.last_seen,
                "count": d.count,
                "last_ua": d.last_ua,
                "last_ip": d.last_ip,
            }
            for d in rows
        ],
    }


class ApproveDeviceBody(BaseModel):
    label: str = Field(default="", max_length=80)


class ApproveDeviceResponse(BaseModel):
    ok: bool
    id: str
    device: DeviceView


@router.post(
    "/keys/{key_id}/devices/{fingerprint}/approve",
    response_model=ApproveDeviceResponse,
    dependencies=[Depends(require_roles("writer")), Depends(require_mfa())],
)
async def approve_key_device(
    key_id: str,
    fingerprint: str,
    body: ApproveDeviceBody,
    request: Request,
) -> dict[str, Any]:
    """Promote a pending device to approved.

    Step-up MFA is required because approving a device widens what
    can authenticate with this token. Approving an already-approved
    device is a no-op that just refreshes the label.
    """
    from ..dry_run import is_dry_run, preview
    tenant = current_tenant_id(request)
    existing = next(
        (p for p in pat_store.live_for_tenant(tenant) if p.id == key_id), None
    )
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="key not found"
        )
    device = pat_trusted_devices.get_device(tenant, key_id, fingerprint)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="device not found"
        )
    if is_dry_run(request):
        return preview(
            "api_key_device_approval_grant",
            key_id,
            tenant_id=tenant,
            name=existing.name,
            fingerprint=fingerprint,
            previous_status=device.status,
            label=body.label or device.label,
        )
    updated = pat_trusted_devices.approve(
        tenant_id=tenant,
        pat_id=key_id,
        fingerprint=fingerprint,
        label=body.label,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="device not found"
        )
    return {
        "ok": True,
        "id": key_id,
        "device": {
            "fingerprint": updated.fingerprint,
            "status": updated.status,
            "label": updated.label,
            "first_seen": updated.first_seen,
            "last_seen": updated.last_seen,
            "count": updated.count,
            "last_ua": updated.last_ua,
            "last_ip": updated.last_ip,
        },
    }


@router.delete(
    "/keys/{key_id}/devices/{fingerprint}",
    dependencies=[Depends(require_roles("writer")), Depends(require_mfa())],
)
async def revoke_key_device(
    key_id: str, fingerprint: str, request: Request
) -> dict[str, Any]:
    """Forget a device (approved or pending).

    A revoked device will appear again as pending the next time the
    token is used from the same network and User-Agent family. Use
    this to clean up a pending sighting that came from a typo'd CI
    job, or to remove an approved device that should no longer have
    access (lost laptop, departed contractor).
    """
    from ..dry_run import is_dry_run, preview
    tenant = current_tenant_id(request)
    existing = next(
        (p for p in pat_store.live_for_tenant(tenant) if p.id == key_id), None
    )
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="key not found"
        )
    device = pat_trusted_devices.get_device(tenant, key_id, fingerprint)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="device not found"
        )
    if is_dry_run(request):
        return preview(
            "api_key_device_revoke",
            key_id,
            tenant_id=tenant,
            name=existing.name,
            fingerprint=fingerprint,
            previous_status=device.status,
        )
    ok = pat_trusted_devices.revoke(
        tenant_id=tenant, pat_id=key_id, fingerprint=fingerprint
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="device not found"
        )
    return {"ok": True, "id": key_id, "fingerprint": fingerprint}


class OwnerEmailBody(BaseModel):
    owner_email: str = Field(
        default="",
        max_length=128,
        description=(
            "Contact email for the human who owns this token. Pass an "
            "empty string to clear the value. Non-empty input must look "
            "like name@example.com."
        ),
    )


class OwnerEmailView(BaseModel):
    ok: bool
    id: str
    owner_email: str


@router.put(
    "/admin/keys/{key_id}/owner-email",
    response_model=OwnerEmailView,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def set_key_owner_email(
    key_id: str, body: OwnerEmailBody, request: Request
) -> dict[str, Any]:
    """Set or clear the owner-email contact on a PAT.

    Admin role plus a fresh MFA step-up is required because shifting
    accountability for a credential is a sensitive operation: a
    half-rotated PAT could be reassigned to someone who no longer
    owns it. Tenant scoping is enforced inside ``pat_store`` so a
    cross-workspace id returns 404 without leaking existence. The
    mutation is captured by the audit middleware automatically.
    """
    from ..dry_run import is_dry_run, preview
    tenant = current_tenant_id(request)
    existing = next(
        (p for p in pat_store.live_for_tenant(tenant) if p.id == key_id), None
    )
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="key not found"
        )
    try:
        safe = pat_store.normalise_owner_email(body.owner_email, allow_blank=True)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_owner_email", "message": str(exc)},
        )
    if is_dry_run(request):
        return preview(
            "api_key_owner_email_set",
            key_id,
            tenant_id=tenant,
            name=existing.name,
            previous_owner_email=existing.owner_email,
            owner_email=safe,
        )
    updated = pat_store.set_owner_email(
        tenant_id=tenant, pat_id=key_id, owner_email=safe
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="key not found"
        )
    return {"ok": True, "id": updated.id, "owner_email": updated.owner_email}


class DescriptionBody(BaseModel):
    description: str = Field(
        default="",
        max_length=200,
        description=(
            "Free-text purpose / runbook note for this token. Pass an "
            "empty string to clear the value. Non-empty input is "
            "whitespace-collapsed and capped at 200 chars."
        ),
    )


class DescriptionView(BaseModel):
    ok: bool
    id: str
    description: str


@router.put(
    "/admin/keys/{key_id}/description",
    response_model=DescriptionView,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def set_key_description(
    key_id: str, body: DescriptionBody, request: Request
) -> dict[str, Any]:
    """Set or clear the purpose / runbook description on a PAT.

    Admin role plus a fresh MFA step-up is required because the
    description is the answer a SOC2 reviewer reads to decide whether
    a credential's continued existence is justified, and a stale or
    misleading description silently weakens that review. Tenant scoping
    is enforced inside ``pat_store`` so a cross-workspace id returns
    404 without leaking existence. The mutation is captured by the
    audit middleware automatically.
    """
    from ..dry_run import is_dry_run, preview
    tenant = current_tenant_id(request)
    existing = next(
        (p for p in pat_store.live_for_tenant(tenant) if p.id == key_id), None
    )
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="key not found"
        )
    try:
        safe = pat_store.normalise_description(body.description)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_description", "message": str(exc)},
        )
    if is_dry_run(request):
        return preview(
            "api_key_description_set",
            key_id,
            tenant_id=tenant,
            name=existing.name,
            previous_description=existing.description,
            description=safe,
        )
    updated = pat_store.set_description(
        tenant_id=tenant, pat_id=key_id, description=safe
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="key not found"
        )
    return {"ok": True, "id": updated.id, "description": updated.description}


class InventoryRow(BaseModel):
    id: str
    name: str
    roles: list[str]
    owner_email: str
    has_owner: bool
    description: str = ""
    has_description: bool = False
    created_at: float
    last_used_at: float
    expires_at: float
    expired: bool


class InventoryResponse(BaseModel):
    total: int
    with_owner: int
    without_owner: int
    with_description: int = 0
    without_description: int = 0
    rows: list[InventoryRow]


@router.get(
    "/admin/keys/inventory",
    response_model=InventoryResponse,
    dependencies=[Depends(require_roles("admin"))],
)
async def keys_inventory(request: Request) -> dict[str, Any]:
    """Workspace credential ownership inventory.

    Lists every live PAT in the current workspace with the owner-email
    contact, so a SOC2 / ISO 27001 reviewer can identify dormant or
    unowned credentials. Read-only and scoped to the calling tenant.
    """
    tenant = current_tenant_id(request)
    pats = pat_store.live_for_tenant(tenant)
    rows: list[dict[str, Any]] = []
    with_owner = 0
    with_description = 0
    for p in pats:
        has_owner = bool(p.owner_email)
        has_description = bool(p.description)
        if has_owner:
            with_owner += 1
        if has_description:
            with_description += 1
        rows.append({
            "id": p.id,
            "name": p.name,
            "roles": sorted(p.roles),
            "owner_email": p.owner_email,
            "has_owner": has_owner,
            "description": p.description,
            "has_description": has_description,
            "created_at": p.created_at,
            "last_used_at": p.last_used_at,
            "expires_at": p.expires_at,
            "expired": p.is_expired(),
        })
    rows.sort(key=lambda r: (r["has_owner"], r["name"]))
    return {
        "total": len(rows),
        "with_owner": with_owner,
        "without_owner": len(rows) - with_owner,
        "with_description": with_description,
        "without_description": len(rows) - with_description,
        "rows": rows,
    }
