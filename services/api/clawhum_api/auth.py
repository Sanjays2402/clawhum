from __future__ import annotations

from collections.abc import Iterable

from fastapi import Header, HTTPException, Request, status

from clawhum_core.settings import get_settings

from .api_keys import ANON_TENANT_ID, DEV_TENANT_ID, ROLES, SCOPES, scopes_allowed_for_roles, get_registry
from . import closure as workspace_closure
from . import auth_methods_policy
from . import ip_allowlist, pat_auth_lockout, pat_store, sessions as session_store, support_access


def _resolved_client_ip(request: Request, tenant_id: str = "") -> str:
    """Best-effort client IP using the trusted-proxy aware resolver.

    Falls back to the socket peer when the resolver cannot run (e.g.
    in tests with a stub request) so the brute-force counter still
    accumulates against *some* identifier rather than an empty string.
    """
    try:
        headers = list(request.headers.items())
        peer = request.client.host if request.client else ""
        return (
            ip_allowlist.client_ip_from_request(headers, peer, tenant_id=tenant_id or None)
            or peer
            or ""
        )
    except Exception:
        try:
            return request.client.host if request.client else ""
        except Exception:
            return ""


def _enforce_auth_method(tenant_id: str, method: str) -> None:
    """Reject the request when the workspace has disabled this credential class.

    Returns 401 (not 403) with an ``auth_method_disabled`` detail and a
    machine-readable ``X-Auth-Method-Disabled`` header naming the blocked
    method so SDKs can route the error to a runbook ("mint a PAT" or
    "use the SCIM token instead") without scraping the detail string.
    The dev / open registry path bypasses this check because there is
    no real tenant to scope the policy against.
    """
    if not tenant_id or tenant_id in {DEV_TENANT_ID, ANON_TENANT_ID}:
        return
    if auth_methods_policy.is_allowed(tenant_id, method):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"auth_method_disabled: '{method}' is disabled for this workspace",
        headers={"X-Auth-Method-Disabled": method},
    )


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
        _enforce_workspace_closure(request)
        _enforce_support_actor(request)
        return "dev"
    key = registry.lookup(x_api_key)
    if key is None:
        # Fall back to user-minted personal access tokens.
        if pat_store.looks_like_pat(x_api_key):
            # Brute-force defense (SOC2 CC6.7 / ISO 27001 A.9.4.2):
            # check the per-IP PAT lockout BEFORE the secret hash
            # lookup so an attacker who has tripped the threshold
            # cannot keep probing the token space at full speed.
            # A locked IP gets HTTP 429 with Retry-After until the
            # cooldown expires or an admin clears it from
            # /admin/pat-auth-lockout.
            _bf_ip = _resolved_client_ip(request)
            _bf_state = pat_auth_lockout.lock_state(_bf_ip)
            if _bf_state.locked:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        "pat_auth_locked: too many failed personal "
                        "access token auth attempts from this IP; "
                        "retry after the cooldown or ask a workspace "
                        "admin to clear the lock from "
                        "/admin/pat-auth-lockout"
                    ),
                    headers={"Retry-After": str(_bf_state.retry_after)},
                )
            pat = pat_store.lookup_by_secret(x_api_key)
            if pat is not None:
                # Per-workspace force-rotation policy: reject any PAT
                # whose created_at exceeds max_pat_age_minutes with a
                # deterministic 401 detail so SDKs can route the error
                # to a "rotate this token" runbook. The token is not
                # auto-revoked: the owner has to mint a fresh secret
                # via rotate, which preserves the existing audit trail.
                _policy = session_store.get_policy(
                    pat.tenant_id or ANON_TENANT_ID
                )
                if session_store.pat_aged_out(
                    created_at=pat.created_at, policy=_policy
                ):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail=(
                            "pat_aged_out: this personal access token "
                            "exceeds the workspace max age policy; "
                            "rotate it from /settings/keys"
                        ),
                    )
                if session_store.pat_idle_revoked(
                    last_used_at=pat.last_used_at,
                    created_at=pat.created_at,
                    policy=_policy,
                ):
                    # Idle / unused credential revocation. SOC2 CC6.1
                    # "deactivate unused credentials" control. Token
                    # is not auto-deleted: the owner has to mint a
                    # replacement (which preserves the audit trail).
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail=(
                            "pat_idle_revoked: this personal access "
                            "token has been unused longer than the "
                            "workspace idle policy allows; mint a new "
                            "token from /settings/keys"
                        ),
                    )
                _enforce_auth_method(pat.tenant_id or ANON_TENANT_ID, "pat")
                request.state.api_key_name = f"pat:{pat.name}"
                request.state.api_key_roles = pat.roles
                request.state.api_key_scopes = pat.effective_scopes()
                request.state.tenant_id = pat.tenant_id or ANON_TENANT_ID
                request.state.pat_id = pat.id
                # Stash the absolute expiry so the response-side
                # PAT expiry warning middleware can attach Sunset /
                # Deprecation headers without re-loading the PAT.
                request.state.pat_expires_at = float(pat.expires_at or 0.0)
                # Enforce the per-PAT IP allowlist BEFORE touching
                # last_used so a denied request does not look like a
                # successful use of the token. The workspace allowlist
                # is still enforced separately below; both gates must
                # pass for the request to proceed.
                _enforce_pat_ip_allowlist(request, pat)
                _enforce_pat_path_prefixes(request, pat)
                # Per-PAT trusted-device strict mode. When the owner
                # has flipped this on, only approved device
                # fingerprints may use the token; everything else is
                # rejected with 403 and the unknown device is
                # recorded so the owner can approve it from
                # /settings/keys. We enforce BEFORE touching
                # last_used so a denied request does not look like a
                # successful use of the token.
                _enforce_pat_trusted_device(request, pat)
                # Best-effort, fire and forget. Failures must never block auth.
                try:
                    headers = list(request.headers.items())
                    client_host = request.client.host if request.client else ""
                    resolved_ip = (
                        ip_allowlist.client_ip_from_request(headers, client_host, tenant_id=pat.tenant_id)
                        or client_host
                        or ""
                    )
                    ua = request.headers.get("user-agent", "")
                    pat_store.touch_last_used(
                        pat.id,
                        client_ip=resolved_ip,
                        user_agent=ua,
                    )
                    # Record this IP in the per-PAT history so admins
                    # can spot a leaked token used from multiple sources.
                    # Strictly best effort; auth must not fail because
                    # the forensic store is unwriteable.
                    try:
                        from . import pat_ip_history
                        pat_ip_history.record(
                            tenant_id=pat.tenant_id,
                            pat_id=pat.id,
                            ip=resolved_ip,
                            user_agent=ua,
                        )
                    except Exception:
                        pass
                except Exception:
                    pass
                _enforce_ip_allowlist(request)
                _record_and_enforce_session(request, actor=f"pat:{pat.name}", actor_kind="pat")
                _enforce_workspace_closure(request)
                _enforce_support_actor(request)
                # Successful PAT auth clears the per-IP failure
                # counter so a legitimate user who fat-fingered a
                # secret before pasting the right one is not
                # penalised by future requests.
                try:
                    pat_auth_lockout.clear(
                        _bf_ip,
                        tenant_id=pat.tenant_id or ANON_TENANT_ID,
                        reason="success",
                    )
                except Exception:
                    pass
                return x_api_key
            # Invalid pat_-prefixed secret: count this attempt against
            # the source IP. The tenant is unknown (the secret did
            # not match any stored hash) so we record an empty
            # tenant_id; admins still see the lock in the global view.
            try:
                pat_auth_lockout.record_failure(_bf_ip, tenant_id="")
            except Exception:
                pass
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")
    _enforce_auth_method(key.tenant_id or ANON_TENANT_ID, "env_key")
    request.state.api_key_name = key.name
    request.state.api_key_roles = key.roles
    request.state.api_key_scopes = scopes_allowed_for_roles(key.roles)
    request.state.tenant_id = key.tenant_id or ANON_TENANT_ID
    # Tenant affinity: tag the source IP with this workspace so the
    # PAT brute-force admin overview can later attribute an unknown-
    # tenant lock to the workspaces that have ever used the IP. This
    # keeps cross-tenant isolation honest: a workspace admin only sees
    # IPs that have at least once authenticated against their own
    # workspace, not every locked IP across the deployment.
    try:
        pat_auth_lockout.tag_tenant(
            _resolved_client_ip(request, tenant_id=key.tenant_id or ""),
            tenant_id=key.tenant_id or ANON_TENANT_ID,
        )
    except Exception:
        pass
    _enforce_ip_allowlist(request)
    _record_and_enforce_session(request, actor=key.name, actor_kind="key")
    _enforce_workspace_closure(request)
    _enforce_support_actor(request)
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
        x_mfa_session: str = Header(default=""),
    ) -> str:
        await role_dep(request, x_api_key=x_api_key)
        return await mfa_dep(
            request,
            x_api_key=x_api_key,
            x_mfa_code=x_mfa_code,
            x_mfa_session=x_mfa_session,
        )

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
    ip = ip_allowlist.client_ip_from_request(headers, client_host, tenant_id=tenant_id) or client_host or ""
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


def _enforce_pat_trusted_device(request: Request, pat: pat_store.PAT) -> None:
    """Reject when strict device approval is on and the device is unknown.

    Computes a stable fingerprint from the resolved client IP (already
    XFF-aware) and a coarse User-Agent family, then looks it up in
    the per-PAT trusted-device list. Approved devices have their
    last_seen / count refreshed and the request proceeds. Unknown or
    pending devices are recorded as pending and the request is
    rejected 403 with a deterministic detail plus an
    ``X-Device-Fingerprint`` header so SDKs can show the owner the
    exact fingerprint to approve.
    """
    if not pat.require_device_approval:
        return
    from . import pat_trusted_devices
    headers = {k.decode().lower(): v.decode() for k, v in request.headers.raw}
    client_host = request.client.host if request.client else ""
    ip = (
        ip_allowlist.client_ip_from_request(headers, client_host, tenant_id=pat.tenant_id)
        or client_host
        or ""
    )
    ua = headers.get("user-agent", "")
    fp = pat_trusted_devices.compute_fingerprint(ip, ua)
    request.state.device_fingerprint = fp
    if pat_trusted_devices.is_approved(pat.tenant_id, pat.id, fp):
        try:
            pat_trusted_devices.touch_approved(
                tenant_id=pat.tenant_id,
                pat_id=pat.id,
                fingerprint=fp,
                ip=ip,
                user_agent=ua,
            )
        except Exception:
            pass
        return
    try:
        pat_trusted_devices.record_pending(
            tenant_id=pat.tenant_id,
            pat_id=pat.id,
            fingerprint=fp,
            ip=ip,
            user_agent=ua,
        )
    except Exception:
        pass
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"device {fp} not approved for this token; "
            "the workspace owner must approve it from "
            "/settings/keys before this device can be used"
        ),
        headers={"X-Device-Fingerprint": fp},
    )


def _enforce_pat_ip_allowlist(request: Request, pat: pat_store.PAT) -> None:
    """Reject when the caller's IP is outside the per-PAT allowlist.

    Empty allowlist means "no restriction", matching how the workspace
    allowlist behaves. ``X-Forwarded-For`` is only honoured when the
    socket peer is in the trusted proxy list (deployment global plus
    per workspace); otherwise the socket peer wins and any forwarding
    header is ignored, so a direct client cannot spoof its source IP
    to bypass the per-PAT allowlist.
    """
    if not pat.ip_cidrs:
        return
    headers = {k.decode().lower(): v.decode() for k, v in request.headers.raw}
    client_host = request.client.host if request.client else None
    client_ip = ip_allowlist.client_ip_from_request(headers, client_host, tenant_id=pat.tenant_id)
    if not pat_store.ip_in_cidrs(client_ip, pat.ip_cidrs):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"ip {client_ip} not in pat allowlist",
        )


def _enforce_pat_path_prefixes(request: Request, pat: pat_store.PAT) -> None:
    """Reject when the request path is outside the per-PAT allowlist.

    Empty allowlist means "no restriction". The allowlist is layered
    on top of scopes so a token holder cannot bypass a wider scope
    grant by hitting an unrelated route. A small carve-out (see
    ``pat_store._PATH_PREFIX_ALWAYS_ALLOWED``) keeps /me, /mfa,
    /sessions, and /keys/policy reachable so a pinned token can
    always rotate itself; if those went dark the runbook for a
    leaked token would deadlock.
    """
    if not pat.path_prefixes:
        return
    path = request.url.path or ""
    if not pat_store.path_matches_allowlist(path, pat.path_prefixes):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"path {path} not in pat allowlist",
            headers={"X-Pat-Path-Denied": path[:200]},
        )


def _enforce_workspace_closure(request: Request) -> None:
    """Reject mutating requests while the workspace is scheduled for
    closure (HTTP 423), and reject all non-export requests once the
    workspace has fully closed (HTTP 410).

    Runs after tenant resolution so the decision is per-workspace.
    Closure routes themselves stay reachable so an admin can always
    cancel a scheduled closure, and the audit / privacy / me / mfa
    surfaces stay reachable so the customer can finish exporting
    data and rotate credentials right up to the finalize timestamp.
    """
    tenant_id = getattr(request.state, "tenant_id", "") or ""
    if not tenant_id or tenant_id == ANON_TENANT_ID:
        return
    decision = workspace_closure.evaluate(
        tenant_id=tenant_id,
        method=request.method,
        path=request.url.path or "",
    )
    if decision is None:
        return
    status_code, detail, body = decision
    headers = {}
    finalize_at = body.get("finalize_at")
    if finalize_at is not None:
        headers["X-Workspace-Finalize-At"] = str(finalize_at)
    state = body.get("state") or ""
    if state:
        headers["X-Workspace-State"] = state
    raise HTTPException(
        status_code=status_code,
        detail=detail,
        headers=headers,
    )


def _enforce_support_actor(request: Request) -> None:
    """When ``X-Support-Actor`` is set, require an active grant.

    The header is the contract between clawhum support staff and the
    customer: support staff identify themselves by email on every
    request made on the customer's behalf, and the customer
    pre-approves which support emails are allowed and for how long.
    No active grant means the request is rejected 403 before any
    business logic runs. When a grant exists, the matching grant id
    and the support actor's email are stamped on request.state so the
    AuditLogMiddleware records every mutating action under that grant
    automatically. The customer keeps the resulting audit chain as
    forensic evidence that vendor access was approved, scoped, and
    time-boxed.

    Read grants restrict the support actor to safe HTTP methods
    (GET, HEAD, OPTIONS); write grants permit any method. A request
    that violates the scope returns 403 with a deterministic detail
    so the support staffer can ask the customer to upgrade the grant
    instead of silently failing on a downstream permission check.
    """
    actor_header = request.headers.get("x-support-actor") or ""
    actor = actor_header.strip().lower()
    if not actor:
        return
    tenant_id = getattr(request.state, "tenant_id", "") or ""
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="support actor header requires an authenticated tenant",
        )
    grant = support_access.find_active_for_actor(tenant_id, actor)
    if grant is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"no active support access grant for {actor} in this "
                "workspace; the workspace owner must approve access "
                "from /settings/support-access before this request "
                "can proceed"
            ),
        )
    if not grant.permits_method(request.method):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"support grant {grant.id} is read-only; ask the "
                "workspace owner to upgrade the grant scope to write "
                "before retrying this request"
            ),
        )
    request.state.support_actor = actor
    request.state.support_grant_id = grant.id
    request.state.support_grant_scope = grant.scope


def _enforce_ip_allowlist(request: Request) -> None:
    """Reject the request when the caller's IP is outside the tenant rules.

    No-op when allowlist enforcement is disabled globally or the tenant
    has not configured any rules. ``X-Forwarded-For`` is only honoured
    when the socket peer is in the trusted proxy list (deployment
    global plus per workspace); otherwise the socket peer wins, so a
    direct client cannot spoof its source IP to bypass the workspace
    allowlist.
    """
    settings = get_settings()
    if not settings.ip_allowlist_enabled:
        return
    tenant_id = getattr(request.state, "tenant_id", "") or ""
    if not tenant_id or not ip_allowlist.has_rules(tenant_id):
        return
    headers = {k.decode().lower(): v.decode() for k, v in request.headers.raw}
    client_host = request.client.host if request.client else None
    client_ip = ip_allowlist.client_ip_from_request(headers, client_host, tenant_id=tenant_id)
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
        x_mfa_session: str = Header(default=""),
    ) -> str:
        from . import mfa  # deferred to avoid circular import at package load
        from . import mfa_lockout
        from . import mfa_session
        from . import audit
        await require_api_key(request, x_api_key=x_api_key)
        settings = get_settings()
        if not settings.mfa_required_for_admin:
            return x_api_key or "dev"
        actor_id = mfa.actor_id_for(x_api_key)
        if not mfa.is_required(actor_id):
            request.state.mfa_used = False
            return x_api_key or "dev"
        tenant_id = getattr(request.state, "tenant_id", "") or ""
        # Step-up session token short-circuit. A valid token issued by
        # POST /mfa/session within the workspace TTL stands in for a
        # fresh code. The token is HMAC-bound to (tenant_id, actor_id)
        # so a leak cannot replay against a different actor, and the
        # per-actor revocation epoch invalidates every outstanding
        # token in O(1) when MFA is disabled or sessions force-logout.
        if x_mfa_session:
            result = mfa_session.verify(
                x_mfa_session, tenant_id=tenant_id, actor_id=actor_id,
            )
            if result.valid:
                request.state.mfa_used = True
                request.state.mfa_session_used = True
                return x_api_key or "dev"
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"mfa session invalid: {result.reason}",
                headers={"WWW-Authenticate": "MFA"},
            )
        # Lockout takes precedence over the MFA check so an attacker
        # cannot keep observing whether a guess would have worked
        # while the cooldown is active.
        pre = mfa_lockout.lock_state(actor_id)
        if pre.locked:
            retry = pre.retry_after
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="mfa locked: too many failed attempts",
                headers={
                    "Retry-After": str(retry),
                    "WWW-Authenticate": "MFA",
                },
            )
        if not x_mfa_code:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="mfa code required",
                headers={"WWW-Authenticate": "MFA"},
            )
        if not mfa.verify(actor_id, x_mfa_code):
            after = mfa_lockout.record_failure(actor_id, tenant_id=tenant_id)
            audit.write_event(
                {
                    "event": "mfa.failed",
                    "actor_id": actor_id,
                    "tenant_id": tenant_id,
                    "path": request.url.path,
                    "failures": after.failures,
                    "locked": after.locked,
                }
            )
            if after.locked:
                audit.write_event(
                    {
                        "event": "mfa.locked",
                        "actor_id": actor_id,
                        "tenant_id": tenant_id,
                        "locked_until": after.locked_until,
                    }
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="mfa locked: too many failed attempts",
                    headers={
                        "Retry-After": str(after.retry_after),
                        "WWW-Authenticate": "MFA",
                    },
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="invalid mfa code",
            )
        # Successful verify clears the failure counter so a user who
        # mistyped twice before getting it right is not penalised.
        mfa_lockout.clear(actor_id, tenant_id=tenant_id, reason="success")
        request.state.mfa_used = True
        return x_api_key or "dev"

    return _dep
