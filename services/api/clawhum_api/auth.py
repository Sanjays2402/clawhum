from __future__ import annotations

from fastapi import Header, HTTPException, Request, status

from .api_keys import get_registry


async def require_api_key(
    request: Request,
    x_api_key: str = Header(default=""),
) -> str:
    """Authenticate a request via X-API-Key.

    Supports the legacy single-key mode and the new multi-key registry.
    When no keys are configured (dev), every request is allowed and the
    actor is recorded as "dev".
    """
    registry = get_registry()
    if registry.is_open():
        request.state.api_key_name = "dev"
        return "dev"
    key = registry.lookup(x_api_key)
    if key is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")
    request.state.api_key_name = key.name
    return x_api_key
