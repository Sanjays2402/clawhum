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
from ..auth import require_roles
from ..tenant import current_tenant_id

router = APIRouter(tags=["keys"])


class CreateKeyBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    roles: list[str] | None = None  # defaults to caller's roles intersected with ROLES
    rpm: int | None = Field(default=0, ge=0, le=100_000)


class KeyView(BaseModel):
    id: str
    name: str
    roles: list[str]
    rpm: int
    created_at: float
    last_used_at: float
    secret_hint: str


class KeyCreateResponse(KeyView):
    secret: str  # plaintext, shown ONCE


@router.get(
    "/keys",
    response_model=list[KeyView],
    dependencies=[Depends(require_roles("writer"))],
)
async def list_keys(request: Request) -> list[dict[str, Any]]:
    tenant = current_tenant_id(request)
    return [pat_store.public_view(p) for p in pat_store.live_for_tenant(tenant)]


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
    )
    view = pat_store.public_view(pat)
    view["secret"] = secret
    return view


@router.delete(
    "/keys/{key_id}",
    dependencies=[Depends(require_roles("writer"))],
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
