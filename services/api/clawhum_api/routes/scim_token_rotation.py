"""Per-workspace SCIM bearer token max-age administration.

Reader role can GET the current policy so the admin dashboard renders
the configured ceiling alongside the age of the active SCIM token.
Admin + step-up MFA is required to PUT a new policy. Tenant scoped on
every call so workspace A cannot read or mutate workspace B's knob.

Sibling routes that live behind this policy:

* ``POST /admin/scim/token``  mints a new token (in routes/scim_admin)
* ``GET  /scim/v2/*``         every read attaches Sunset/Deprecation
                              headers when the active token is stale

The audit trail lands through the same write_event call every other
per-workspace policy uses, so the global admin audit timeline shows
who flipped the knob and when, with full before/after diff.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import scim_token_rotation, scim_tokens
from ..audit import write_event
from ..auth import require_admin_with_mfa, require_roles
from ..tenant import current_tenant_id


router = APIRouter(
    tags=["scim-token-rotation"], prefix="/scim-token-rotation"
)


class PolicyResponse(BaseModel):
    enforcing: bool
    max_token_age_days: int = 0
    docs_url: str = ""
    updated_at: float = 0.0
    updated_by: str = ""
    token_configured: bool = False
    token_created_at: float = 0.0
    token_age_days: int = 0
    token_is_stale: bool = False
    example_headers: dict[str, str] = Field(default_factory=dict)


class PolicyUpdate(BaseModel):
    max_token_age_days: int = Field(
        default=0,
        ge=0,
        le=3650,
        description=(
            "Maximum age in days for the active SCIM bearer token "
            "before the API starts attaching Sunset/Deprecation "
            "headers to every SCIM response. 0 disables the policy."
        ),
    )
    docs_url: str = Field(
        default="",
        max_length=512,
        description=(
            "Optional http(s) URL surfaced in the Link: rel=sunset "
            "header to point IdP operators at the rotation runbook."
        ),
    )


def _actor_id(request: Request) -> str:
    return (
        getattr(request.state, "api_key_name", "")
        or getattr(request.state, "pat_id", "")
        or "unknown"
    )


def _to_response(request: Request) -> PolicyResponse:
    tenant = current_tenant_id(request)
    pol = scim_token_rotation.get_policy(tenant)
    days = pol.max_token_age_days if pol else 0
    docs = pol.docs_url if pol else ""

    token_row = scim_tokens.get_active(tenant)
    token_created_at = float(token_row.created_at) if token_row else 0.0
    now = time.time()
    token_age_days = (
        int((now - token_created_at) // 86400) if token_created_at > 0 else 0
    )
    is_stale = scim_token_rotation.is_stale(
        tenant_id=tenant,
        token_created_at=token_created_at,
        now=now,
    )
    example: dict[str, str] = {}
    if days > 0:
        # Synthesise a fake-stale created_at so the dashboard can show
        # the exact header set IdP operators would see at the cutoff,
        # even when the real token is still inside the window.
        preview_created = (
            token_created_at
            if (token_created_at > 0 and is_stale)
            else now - (days + 1) * 86400
        )
        example = scim_token_rotation.compute_headers(
            tenant_id=tenant,
            token_created_at=preview_created,
            now=now,
        )
    return PolicyResponse(
        enforcing=bool(days),
        max_token_age_days=days,
        docs_url=docs,
        updated_at=pol.updated_at if pol else 0.0,
        updated_by=pol.updated_by if pol else "",
        token_configured=token_row is not None,
        token_created_at=token_created_at,
        token_age_days=token_age_days,
        token_is_stale=is_stale,
        example_headers=example,
    )


@router.get(
    "",
    response_model=PolicyResponse,
    dependencies=[Depends(require_roles("reader"))],
)
async def get_scim_token_rotation(request: Request) -> PolicyResponse:
    return _to_response(request)


@router.put(
    "",
    response_model=PolicyResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def set_scim_token_rotation(
    body: PolicyUpdate, request: Request
) -> PolicyResponse:
    tenant = current_tenant_id(request)
    actor = _actor_id(request)
    before = scim_token_rotation.get_policy(tenant)
    try:
        saved = scim_token_rotation.set_policy(
            tenant_id=tenant,
            max_token_age_days=body.max_token_age_days,
            docs_url=body.docs_url,
            updated_by=actor,
        )
    except scim_token_rotation.InvalidPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_scim_token_rotation",
                "message": str(exc),
            },
        )
    write_event(
        {
            "ts": saved.updated_at,
            "actor": actor,
            "tenant_id": tenant,
            "action": "scim_token_rotation.update",
            "target": tenant,
            "request_id": getattr(request.state, "request_id", ""),
            "before": before.to_dict() if before else {
                "max_token_age_days": 0,
                "docs_url": "",
            },
            "after": saved.to_dict(),
        }
    )
    return _to_response(request)
