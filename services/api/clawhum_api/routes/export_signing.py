"""Per-workspace export signing key administration.

Endpoints
---------
GET    /export-signing            return public key info (key_id,
                                  created_at, rotation state). Reader+.
POST   /export-signing/mint       create the initial key if none
                                  exists; returns the plaintext secret
                                  exactly once. Admin + MFA.
POST   /export-signing/rotate     rotate the key; returns the new
                                  plaintext secret exactly once and
                                  starts a 14 day grace window for the
                                  previous key so in-flight verifies
                                  still succeed. Admin + MFA.
POST   /export-signing/reveal     re-display the active secret. Admin
                                  + MFA, audited. Useful when the
                                  workspace owner lost the post-mint
                                  copy and prefers re-display over
                                  rotating (which invalidates buyer
                                  verifications after the grace).

Verification of a downloaded bundle lives at
``/v1/privacy/workspace-export/verify`` and is not gated by admin so
a compliance reviewer holding a reader role can still verify a bundle
their workspace owner handed them.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request

from .. import export_signing
from ..audit import write_event
from ..auth import require_admin_with_mfa, require_roles
from ..tenant import current_tenant_id

router = APIRouter(tags=["export-signing"], prefix="/export-signing")


def _actor_id(request: Request) -> str:
    return (
        getattr(request.state, "api_key_name", "")
        or getattr(request.state, "pat_id", "")
        or "unknown"
    )


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or ""


@router.get("", dependencies=[Depends(require_roles("reader"))])
async def get_signing_key(request: Request) -> dict:
    tenant = current_tenant_id(request)
    key = export_signing.get_key(tenant)
    if key is None:
        return {
            "tenant_id": tenant,
            "exists": False,
            "algorithm": export_signing.SIGNING_ALG,
            "verify_endpoint": "/v1/privacy/workspace-export/verify",
        }
    return {
        "exists": True,
        "verify_endpoint": "/v1/privacy/workspace-export/verify",
        **key.public_view(),
    }


@router.post(
    "/mint",
    dependencies=[Depends(require_admin_with_mfa())],
)
async def mint_signing_key(request: Request) -> dict:
    tenant = current_tenant_id(request)
    actor = _actor_id(request)
    existing = export_signing.get_key(tenant)
    if existing is not None:
        return {
            "minted": False,
            "reason": "key already exists; use /export-signing/rotate to replace it",
            **existing.public_view(),
        }
    key, secret = export_signing.ensure_key(tenant, created_by=actor)
    write_event(
        {
            "ts": time.time(),
            "actor": actor,
            "tenant_id": tenant,
            "action": "export_signing.mint",
            "target": key.key_id,
            "request_id": _request_id(request),
            "before": {},
            "after": key.public_view(),
        }
    )
    return {
        "minted": True,
        "secret": secret,
        "warning": "Store this secret in your secrets manager. It will not be shown again unless you call /export-signing/reveal.",
        **key.public_view(),
    }


@router.post(
    "/rotate",
    dependencies=[Depends(require_admin_with_mfa())],
)
async def rotate_signing_key(request: Request) -> dict:
    tenant = current_tenant_id(request)
    actor = _actor_id(request)
    before = export_signing.get_key(tenant)
    key, secret = export_signing.rotate(tenant, created_by=actor)
    write_event(
        {
            "ts": time.time(),
            "actor": actor,
            "tenant_id": tenant,
            "action": "export_signing.rotate",
            "target": key.key_id,
            "request_id": _request_id(request),
            "before": before.public_view() if before else {},
            "after": key.public_view(),
        }
    )
    return {
        "rotated": True,
        "secret": secret,
        "grace_seconds": export_signing.GRACE_SECONDS,
        "warning": "Previous key still verifies for 14 days, then becomes invalid. Re-archive any bundle you signed under the previous key with a fresh export.",
        **key.public_view(),
    }


@router.post(
    "/reveal",
    dependencies=[Depends(require_admin_with_mfa())],
)
async def reveal_signing_secret(request: Request) -> dict:
    tenant = current_tenant_id(request)
    actor = _actor_id(request)
    try:
        secret = export_signing.reveal_active_secret(tenant)
    except LookupError:
        return {
            "exists": False,
            "reason": "no signing key for tenant; call /export-signing/mint first",
        }
    key = export_signing.get_key(tenant)
    assert key is not None
    write_event(
        {
            "ts": time.time(),
            "actor": actor,
            "tenant_id": tenant,
            "action": "export_signing.reveal",
            "target": key.key_id,
            "request_id": _request_id(request),
            "before": {},
            "after": {"key_id": key.key_id},
        }
    )
    return {"secret": secret, **key.public_view()}
