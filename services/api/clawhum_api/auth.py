from __future__ import annotations

from collections.abc import Iterable

from fastapi import Header, HTTPException, Request, status

from clawhum_core.settings import get_settings

from .api_keys import ANON_TENANT_ID, DEV_TENANT_ID, ROLES, get_registry
from . import ip_allowlist, pat_store


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
                request.state.tenant_id = pat.tenant_id or ANON_TENANT_ID
                request.state.pat_id = pat.id
                # Best-effort, fire and forget. Failures must never block auth.
                try:
                    pat_store.touch_last_used(pat.id)
                except Exception:
                    pass
                _enforce_ip_allowlist(request)
                return x_api_key
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")
    request.state.api_key_name = key.name
    request.state.api_key_roles = key.roles
    request.state.tenant_id = key.tenant_id or ANON_TENANT_ID
    _enforce_ip_allowlist(request)
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
