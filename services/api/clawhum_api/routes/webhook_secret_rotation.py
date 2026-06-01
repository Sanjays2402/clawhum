"""Per-workspace webhook signing-secret max-age administration.

Reader role can GET the current policy so the dashboard renders the
configured ceiling alongside the list of webhooks whose secret has
crossed it. Admin + MFA is required to PUT a new policy. Tenant
scoped on every call so workspace A cannot mutate workspace B.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from .. import webhook_secret_rotation
from ..audit import write_event
from ..auth import require_admin_with_mfa, require_roles
from ..tenant import current_tenant_id


router = APIRouter(tags=["webhook-secret-rotation"], prefix="/webhook-secret-rotation")


class PolicyResponse(BaseModel):
    enforcing: bool
    max_secret_age_days: int = 0
    docs_url: str = ""
    updated_at: float = 0.0
    updated_by: str = ""
    stale_count: int = 0
    example_headers: dict[str, str] = Field(default_factory=dict)


class PolicyUpdate(BaseModel):
    max_secret_age_days: int = Field(
        default=0,
        ge=0,
        le=3650,
        description=(
            "Maximum age in days for a webhook signing secret before "
            "the API starts attaching Sunset/Deprecation headers to "
            "webhook listing responses. 0 disables the policy."
        ),
    )
    docs_url: str = Field(
        default="",
        max_length=512,
        description=(
            "Optional http(s) URL surfaced in the Link: rel=sunset "
            "header to point operators at the rotation runbook."
        ),
    )


class StaleHookItem(BaseModel):
    id: str
    url: str
    secret_age_days: int
    rotated_at: float = 0.0
    created_at: float = 0.0


class StaleHookList(BaseModel):
    items: list[StaleHookItem]
    max_secret_age_days: int


def _actor_id(request: Request) -> str:
    return (
        getattr(request.state, "api_key_name", "")
        or getattr(request.state, "pat_id", "")
        or "unknown"
    )


def _hooks_for(tenant_id: str) -> list[dict]:
    # Localised import: the webhooks route module owns hook storage.
    from . import webhooks as webhooks_routes

    return webhooks_routes._live_hooks(tenant_id)


def _to_response(request: Request) -> PolicyResponse:
    tenant = current_tenant_id(request)
    pol = webhook_secret_rotation.get_policy(tenant)
    days = pol.max_secret_age_days if pol else 0
    docs = pol.docs_url if pol else ""
    hooks = _hooks_for(tenant) if days > 0 else []
    stale = webhook_secret_rotation.stale_hooks(tenant_id=tenant, hooks=hooks)
    example: dict[str, str] = {}
    if days > 0:
        # Synthesise a single fake-stale hook to preview the header
        # set when no real hook is yet over the floor; that way the
        # dashboard never shows an empty preview block.
        preview_hooks = hooks if stale else [
            {"rotated_at": 0.0, "created_at": time.time() - (days + 1) * 86400}
        ]
        example = webhook_secret_rotation.compute_headers(
            tenant_id=tenant, hooks=preview_hooks
        )
    return PolicyResponse(
        enforcing=bool(days),
        max_secret_age_days=days,
        docs_url=docs,
        updated_at=pol.updated_at if pol else 0.0,
        updated_by=pol.updated_by if pol else "",
        stale_count=len(stale),
        example_headers=example,
    )


@router.get(
    "",
    response_model=PolicyResponse,
    dependencies=[Depends(require_roles("reader"))],
)
async def get_webhook_secret_rotation(request: Request) -> PolicyResponse:
    return _to_response(request)


@router.put(
    "",
    response_model=PolicyResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def set_webhook_secret_rotation(
    body: PolicyUpdate, request: Request
) -> PolicyResponse:
    tenant = current_tenant_id(request)
    actor = _actor_id(request)
    before = webhook_secret_rotation.get_policy(tenant)
    try:
        saved = webhook_secret_rotation.set_policy(
            tenant_id=tenant,
            max_secret_age_days=body.max_secret_age_days,
            docs_url=body.docs_url,
            updated_by=actor,
        )
    except webhook_secret_rotation.InvalidPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_webhook_secret_rotation",
                "message": str(exc),
            },
        )
    write_event(
        {
            "ts": saved.updated_at,
            "actor": actor,
            "tenant_id": tenant,
            "action": "webhook_secret_rotation.update",
            "target": tenant,
            "request_id": getattr(request.state, "request_id", ""),
            "before": before.to_dict() if before else {
                "max_secret_age_days": 0, "docs_url": ""
            },
            "after": saved.to_dict(),
        }
    )
    return _to_response(request)


@router.get(
    "/stale",
    response_model=StaleHookList,
    dependencies=[Depends(require_roles("reader"))],
)
async def list_stale_webhooks(
    request: Request, response: Response
) -> StaleHookList:
    tenant = current_tenant_id(request)
    pol = webhook_secret_rotation.get_policy(tenant)
    days = pol.max_secret_age_days if pol else 0
    hooks = _hooks_for(tenant)
    stale = webhook_secret_rotation.stale_hooks(tenant_id=tenant, hooks=hooks)
    now = time.time()
    items = [
        StaleHookItem(
            id=str(h.get("id", "")),
            url=str(h.get("url", "")),
            secret_age_days=int(
                (now - webhook_secret_rotation.secret_age_anchor(h)) / 86400
            ),
            rotated_at=float(h.get("rotated_at") or 0.0),
            created_at=float(h.get("created_at") or 0.0),
        )
        for h in stale
    ]
    items.sort(key=lambda i: i.secret_age_days, reverse=True)
    headers = webhook_secret_rotation.compute_headers(
        tenant_id=tenant, hooks=hooks
    )
    for k, v in headers.items():
        response.headers[k] = v
    return StaleHookList(items=items, max_secret_age_days=days)
