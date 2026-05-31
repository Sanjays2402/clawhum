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
from ..api_keys import ROLES
from ..auth import require_mfa, require_roles
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


class KeyView(BaseModel):
    id: str
    name: str
    roles: list[str]
    rpm: int
    created_at: float
    last_used_at: float
    secret_hint: str
    expires_at: float
    expired: bool


class KeyCreateResponse(KeyView):
    secret: str  # plaintext, shown ONCE


class KeyPolicyResponse(BaseModel):
    max_ttl_days: int
    default_ttl_days: int


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
async def keys_policy() -> dict[str, int]:
    """Expose the workspace PAT lifetime policy to the UI.

    The mint form reads this to render the TTL picker so it never
    offers a value the server will reject.
    """
    s = get_settings()
    return {
        "max_ttl_days": int(s.pat_max_ttl_days or 0),
        "default_ttl_days": int(s.pat_default_ttl_days or 0),
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
    pat, secret = pat_store.create(
        tenant_id=tenant,
        name=body.name,
        roles=requested,
        rpm=body.rpm or 0,
        expires_in_days=body.expires_in_days,
    )
    view = pat_store.public_view(pat)
    view["secret"] = secret
    return view


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
