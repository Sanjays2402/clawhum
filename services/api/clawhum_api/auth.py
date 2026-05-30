from __future__ import annotations

from collections.abc import Iterable

from fastapi import Header, HTTPException, Request, status

from .api_keys import ROLES, get_registry


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
        return "dev"
    key = registry.lookup(x_api_key)
    if key is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")
    request.state.api_key_name = key.name
    request.state.api_key_roles = key.roles
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


def _normalise(roles: Iterable[str]) -> frozenset[str]:
    out = frozenset(r.lower() for r in roles if r)
    unknown = out - ROLES
    if unknown:
        raise ValueError(f"unknown role(s): {sorted(unknown)}")
    return out
