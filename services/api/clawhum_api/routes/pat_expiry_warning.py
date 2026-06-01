"""Per-workspace PAT expiry advance-warning administration.

Reader role can GET the current policy so the dashboard renders the
threshold + docs URL alongside an example of the headers a client
will see. Admin + MFA is required to PUT a new policy. Tenant
scoped on every call so workspace A cannot mutate workspace B.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import pat_expiry_warning
from ..audit import write_event
from ..auth import require_admin_with_mfa, require_roles
from ..tenant import current_tenant_id


router = APIRouter(tags=["pat-expiry-warning"], prefix="/pat-expiry-warning")


class PolicyResponse(BaseModel):
    enforcing: bool
    warn_within_days: int = 0
    docs_url: str = ""
    updated_at: float = 0.0
    updated_by: str = ""
    example_headers: dict[str, str] = Field(default_factory=dict)


class PolicyUpdate(BaseModel):
    warn_within_days: int = Field(
        default=0,
        ge=0,
        le=365,
        description=(
            "Days before PAT expiry at which the API should start "
            "attaching Sunset/Deprecation headers to responses "
            "authenticated by that token. 0 disables the policy."
        ),
    )
    docs_url: str = Field(
        default="",
        max_length=512,
        description=(
            "Optional http(s) URL surfaced in the Link: rel=sunset "
            "header to point SDKs at the rotation runbook."
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
    pol = pat_expiry_warning.get_policy(tenant)
    days = pol.warn_within_days if pol else 0
    docs = pol.docs_url if pol else ""
    # Render the headers as they would appear for a PAT that expires
    # halfway through the warning window so the dashboard shows the
    # full set, even when no real PAT is currently inside the window.
    example: dict[str, str] = {}
    if days > 0:
        import time as _t

        fake_expires = _t.time() + (days * 86400 / 2)
        # compute_headers reads the live policy; we cannot pass a
        # synthetic policy without changing its signature, so we just
        # ask the real one for this tenant.
        example = pat_expiry_warning.compute_headers(
            tenant_id=tenant, expires_at=fake_expires
        )
    return PolicyResponse(
        enforcing=bool(days),
        warn_within_days=days,
        docs_url=docs,
        updated_at=pol.updated_at if pol else 0.0,
        updated_by=pol.updated_by if pol else "",
        example_headers=example,
    )


@router.get(
    "",
    response_model=PolicyResponse,
    dependencies=[Depends(require_roles("reader"))],
)
async def get_pat_expiry_warning(request: Request) -> PolicyResponse:
    return _to_response(request)


@router.put(
    "",
    response_model=PolicyResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def set_pat_expiry_warning(
    body: PolicyUpdate, request: Request
) -> PolicyResponse:
    tenant = current_tenant_id(request)
    actor = _actor_id(request)
    before = pat_expiry_warning.get_policy(tenant)
    try:
        saved = pat_expiry_warning.set_policy(
            tenant_id=tenant,
            warn_within_days=body.warn_within_days,
            docs_url=body.docs_url,
            updated_by=actor,
        )
    except pat_expiry_warning.InvalidWarningError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_pat_expiry_warning",
                "message": str(exc),
            },
        )
    write_event(
        {
            "ts": saved.updated_at,
            "actor": actor,
            "tenant_id": tenant,
            "action": "pat_expiry_warning.update",
            "target": tenant,
            "request_id": getattr(request.state, "request_id", ""),
            "before": before.to_dict() if before else {
                "warn_within_days": 0, "docs_url": ""
            },
            "after": saved.to_dict(),
        }
    )
    return _to_response(request)
