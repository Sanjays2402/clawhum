"""Identity and quota introspection for the calling API key.

Returns who the bearer is (tenant, role set, key name) and the
configured per-minute request budget. Lets the web client render a
real usage meter, API key card, and copy-paste curl example without
shipping any secrets to the browser by default.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..api_keys import ANON_TENANT_ID, ROLES, get_registry
from ..auth import require_api_key
from clawhum_core.settings import get_settings
from pydantic import BaseModel

router = APIRouter(tags=["me"])


class MeResponse(BaseModel):
    tenant_id: str
    key_name: str
    roles: list[str]
    rate_limit_per_minute: int
    auth_mode: str  # "open" (dev) or "key"
    masked_key: str  # last 4 chars or "dev"


def _mask(secret: str) -> str:
    if not secret or secret == "dev":
        return "dev"
    if len(secret) <= 4:
        return "*" * len(secret)
    return f"...{secret[-4:]}"


@router.get("/me", response_model=MeResponse, dependencies=[Depends(require_api_key)])
async def me(request: Request) -> MeResponse:
    registry = get_registry()
    settings = get_settings()
    name = getattr(request.state, "api_key_name", "dev")
    tenant = getattr(request.state, "tenant_id", ANON_TENANT_ID)
    roles: frozenset[str] = getattr(request.state, "api_key_roles", ROLES)
    auth_mode = "open" if registry.is_open() else "key"

    # Look up the actual rpm for this key, falling back to the global default.
    rpm = settings.rate_limit_per_minute
    presented = request.headers.get("x-api-key", "")
    key = registry.lookup(presented)
    if key is not None and key.rpm:
        rpm = key.rpm

    return MeResponse(
        tenant_id=tenant,
        key_name=name,
        roles=sorted(roles),
        rate_limit_per_minute=int(rpm),
        auth_mode=auth_mode,
        masked_key=_mask(presented or "dev"),
    )
