from __future__ import annotations

from collections.abc import Iterable

from fastapi import Header, HTTPException, Request, status

from clawhum_core.settings import get_settings

from .api_keys import ANON_TENANT_ID, DEV_TENANT_ID, ROLES, SCOPES, scopes_allowed_for_roles, get_registry
from . import ip_allowlist, pat_store, sessions as session_store


async def require_api_key(
    request: Request,
    x_api_key: str = Header(default=""),
) -> str:
    """Authenticate a request via X-API-Key.

    Supports the legacy single-key mode and the new multi-key registry.
    When no keys are configured (dev), every request is allowed and the
    actor is recorded as "dev" with the full role set.
    """
    registry = get_registry()
    if registry.is_open():
        request.state.api_key_name = "dev"
        request.state.api_key_roles = ROLES
        request.state.api_key_scopes = SCOPES
        request.state.tenant_id = DEV_TENANT_ID
        _enforce_ip_allowlist(request)
        return "dev"
    key = registry.lookup(x_api_key)
    if key is None:
        # Fall back to user-minted personal access tokens.
        if pat_store.looks_like_pat(x_api_key):
            pat = pat_store.lookup_by_secret(x_api_key)
            if pat is not None:
                request.state.api_key_name = f"pat:{pat.name}"
                request.state.api_key_roles = pat.roles
                request.state.api_key_scopes = pat.effective_scopes()
                request.state.tenant_id = pat.tenant_id or ANON_TENANT_ID
                request.state.pat_id = pat.id
                # Enforce the per-PAT IP allowlist BEFORE touching
                # last_used so a denied request does not look like a
                # successful use of the token. The workspace allowlist
                # is still enforced separately below; both gates must
                # pass for the request to proceed.
                _enforce_pat_ip_allowlist(request, pat)
                # Best-effort, fire and forget. Failures must never block auth.
                try:
                    pat_store.touch_last_used(pat.id)
                except Exception:
                    pass
                _enforce_ip_allowlist(request)
                _record_and_enforce_session(request, actor=f"pat:{pat.name}", actor_kind="pat")
                return x_api_key
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")
    request.state.api_key_name = key.name
    request.state.api_key_roles = key.roles
    request.state.api_key_scopes = scopes_allowed_for_roles(key.roles)
    request.state.tenant_id = key.tenant_id or ANON_TENANT_ID
    _enforce_ip_allowlist(request)
    _record_and_enforce_session(request, actor=key.name, actor_kind="key")
    return x_api_key


def require_roles(*roles: str):
    """Build a FastAPI dependency enforcing one of the given roles.

    Authenticates via require_api_key first, then checks role membership.
    "admin" always satisfies any required role. Returns 403 on mismatch
    so clients can distinguish missing creds (401) from missing scope.
    """

    required = _normalise(roles)

    async def _dep(
        request: Request,
        x_api_key: str = Header(default=""),
    ) -> str:
        await require_api_key(request, x_api_key=x_api_key)
        granted: frozenset[str] = getattr(request.state, "api_key_roles", frozenset())
        if "admin" in granted:
            return x_api_key or "dev"
        if not (granted & required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"missing role: one of {sorted(required)}",
            )
        return x_api_key or "dev"

    return _dep


def require_scopes(*scopes: str):
    """Build a FastAPI dependency enforcing PAT-style fine-grained scopes.

    Auth flow: authenticate as usual, then require that the caller's
    effective scope set contains *every* scope listed. PATs minted
    with a narrow scope list (least privilege) will be rejected if
    they try to call a route outside that list, even when their role
    would otherwise permit it. Legacy PATs and API keys minted before
    scopes existed expose the maximum scope set their roles imply,
    so this dependency is backwards compatible.

    The `admin` scope always satisfies any requirement, mirroring the
    `admin` role rule used by ``require_roles``. Returns 403 with the
    list of missing scopes so SDKs can surface a useful error.
    """
    required = frozenset(s.strip().lower() for s in scopes if s)

    async def _dep(
        request: Request,
        x_api_key: str = Header(default=""),
    ) -> str:
        await require_api_key(request, x_api_key=x_api_key)
        granted: frozenset[str] = getattr(
            request.state, "api_key_scopes", frozenset()
        )
        if "admin" in granted:
            return x_api_key or "dev"
        missing = required - granted
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"missing scope(s): {sorted(missing)}",
            )
        return x_api_key or "dev"

    return _dep


def require_admin_with_mfa():
    """Compose ``require_roles('admin')`` with ``require_mfa()``.

    Use this on destructive admin endpoints (revoking sibling keys,
    deleting IP allowlist rules, hard-deleting workspace data,
    rotating webhooks). The role check returns 403 to non-admins;
    the MFA check returns 401 with WWW-Authenticate: MFA when the
    caller has enrolled but did not supply X-MFA-Code, so clients can
    render the step-up prompt without guessing why the call failed.
    """
    role_dep = require_roles("admin")
    mfa_dep = require_mfa()

    async def _dep(
        request: Request,
        x_api_key: str = Header(default=""),
        x_mfa_code: str = Header(default=""),
    ) -> str:
        await role_dep(request, x_api_key=x_api_key)
        return await mfa_dep(request, x_api_key=x_api_key, x_mfa_code=x_mfa_code)

    return _dep


def _record_and_enforce_session(request: Request, *, actor: str, actor_kind: str) -> None:
    """Touch the per-actor session row and reject if policy expired it.

    Two failure modes are surfaced distinctly so SDKs can react:
    an explicit revoke returns 401 with a ``session revoked`` detail,
    and a policy timeout returns 401 with a ``session expired``
    detail plus a ``Session-Expired-Reason`` header naming the bound
    that fired. Both states require the caller to obtain a fresh
    credential (or have an owner clear the revocation), mirroring
    how every enterprise IdP behaves when a session cap fires.
    """
    tenant_id = getattr(request.state, "tenant_id", "") or ""
    if not tenant_id:
        return
    headers = {k.decode().lower(): v.decode() for k, v in request.headers.raw}
    client_host = request.client.host if request.client else ""
    ip = ip_allowlist.client_ip_from_request(headers, client_host) or client_host or ""
    user_agent = headers.get("user-agent", "")
    try:
        sess = session_store.touch(
            tenant_id=tenant_id,
            actor=actor,
            actor_kind=actor_kind,
            ip=ip,
            user_agent=user_agent,
        )
    except Exception:
        # Session tracking failures must never lock a tenant out of
        # their own data; the audit log captures the auth event
        # independently.
        return
    request.state.session_id = sess.id
    if sess.revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="session revoked",
            headers={"WWW-Authenticate": "Session"},
        )
    policy = session_store.get_policy(tenant_id)
    expired, reason = session_store.is_expired(sess, policy)
    if expired:
        # Revoke the row so subsequent requests do not have to
        # recompute the same decision and so the UI surfaces the
        # expiry to the owner.
        session_store.revoke(tenant_id, sess.id, reason=f"policy:{reason}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="session expired",
            headers={
                "WWW-Authenticate": "Session",
                "Session-Expired-Reason": reason,
            },
        )


def _enforce_pat_ip_allowlist(request: Request, pat: pat_store.PAT) -> None:
    """Reject when the caller's IP is outside the per-PAT allowlist.

    Empty allowlist means "no restriction", matching how the workspace
    allowlist behaves. Trusts the first X-Forwarded-For hop when set,
    mirroring ``_enforce_ip_allowlist`` so a deployment behind a single
    trusted proxy honours both gates identically. Operators who do not
    terminate TLS at a trusted edge MUST strip XFF upstream.
    """
    if not pat.ip_cidrs:
        return
    headers = {k.decode().lower(): v.decode() for k, v in request.headers.raw}
    client_host = request.client.host if request.client else None
    client_ip = ip_allowlist.client_ip_from_request(headers, client_host)
    if not pat_store.ip_in_cidrs(client_ip, pat.ip_cidrs):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"ip {client_ip} not in pat allowlist",
        )


def _enforce_ip_allowlist(request: Request) -> None:
    """Reject the request when the caller's IP is outside the tenant rules.

    No-op when allowlist enforcement is disabled globally or the tenant
    has not configured any rules. Trusts the first X-Forwarded-For hop
    so deployments behind a single trusted proxy work out of the box;
    operators terminating TLS elsewhere should strip untrusted XFF
    headers at the edge.
    """
    settings = get_settings()
    if not settings.ip_allowlist_enabled:
        return
    tenant_id = getattr(request.state, "tenant_id", "") or ""
    if not tenant_id or not ip_allowlist.has_rules(tenant_id):
        return
    headers = {k.decode().lower(): v.decode() for k, v in request.headers.raw}
    client_host = request.client.host if request.client else None
    client_ip = ip_allowlist.client_ip_from_request(headers, client_host)
    if not ip_allowlist.is_allowed(tenant_id, client_ip):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"ip {client_ip} not in workspace allowlist",
        )


def _normalise(roles: Iterable[str]) -> frozenset[str]:
    out = frozenset(r.lower() for r in roles if r)
    unknown = out - ROLES
    if unknown:
        raise ValueError(f"unknown role(s): {sorted(unknown)}")
    return out


def require_mfa():
    """Build a dependency that step-up authenticates the caller for
    destructive admin actions when MFA has been enrolled for them.

    Behaviour:
      * Authenticates the request first (reuses require_api_key so the
        usual 401 / IP allowlist rules apply).
      * If the global toggle ``mfa_required_for_admin`` is off, passes.
      * If the actor has not enrolled MFA, passes (adoption is per-actor
        and the dependency must not lock anyone out before they enrol).
      * If the actor has enrolled, requires a valid ``X-MFA-Code`` (TOTP
        or one-shot recovery code). Missing code returns 401 with
        ``WWW-Authenticate: MFA`` so a client can render a step-up prompt.
        Bad code returns 403 so brute force attempts are distinguishable
        in the audit log.
    """

    async def _dep(
        request: Request,
        x_api_key: str = Header(default=""),
        x_mfa_code: str = Header(default=""),
    ) -> str:
        from . import mfa  # deferred to avoid circular import at package load
        await require_api_key(request, x_api_key=x_api_key)
        settings = get_settings()
        if not settings.mfa_required_for_admin:
            return x_api_key or "dev"
        actor_id = mfa.actor_id_for(x_api_key)
        if not mfa.is_required(actor_id):
            request.state.mfa_used = False
            return x_api_key or "dev"
        if not x_mfa_code:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="mfa code required",
                headers={"WWW-Authenticate": "MFA"},
            )
        if not mfa.verify(actor_id, x_mfa_code):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="invalid mfa code",
            )
        request.state.mfa_used = True
        return x_api_key or "dev"

    return _dep
