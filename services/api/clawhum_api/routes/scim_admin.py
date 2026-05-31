"""Admin endpoints for the per-workspace SCIM bearer token.

Why this is a separate router and not folded into routes/scim.py:
``routes/scim.py`` accepts SCIM bearer tokens; this router accepts the
normal workspace admin auth (API key or PAT, role admin, step-up MFA).
Keeping them apart means the SCIM surface never authenticates against
admin creds and vice versa.

Endpoints:
- GET    /admin/scim/token    returns whether a token is configured + metadata
- POST   /admin/scim/token    mints a fresh token, revoking any previous one
- DELETE /admin/scim/token    revokes the current token

All mutations go through the global AuditLogMiddleware so every mint
and revoke is recorded.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from .. import scim_tokens
from ..auth import require_admin_with_mfa, require_roles
from ..tenant import current_tenant_id

router = APIRouter(tags=["scim-admin"], prefix="/admin/scim")


@router.get("/token")
def get_scim_token_status(
    request: Request,
    _: str = Depends(require_roles("admin")),
) -> dict[str, Any]:
    tenant_id = current_tenant_id(request)
    row = scim_tokens.get_active(tenant_id)
    if row is None:
        return {"configured": False}
    return {"configured": True, **row.public_dict()}


@router.post("/token", status_code=201)
def mint_scim_token(
    request: Request,
    _: str = Depends(require_admin_with_mfa()),
) -> dict[str, Any]:
    tenant_id = current_tenant_id(request)
    created_by = getattr(request.state, "api_key_name", "unknown")
    row, token = scim_tokens.mint(tenant_id=tenant_id, created_by=created_by)
    return {
        "configured": True,
        "token": token,  # plaintext, shown ONCE
        **row.public_dict(),
    }


@router.delete("/token", status_code=204)
def revoke_scim_token(
    request: Request,
    _: str = Depends(require_admin_with_mfa()),
) -> None:
    tenant_id = current_tenant_id(request)
    scim_tokens.revoke(tenant_id)
    return None
