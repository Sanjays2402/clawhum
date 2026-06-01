"""Per-workspace request body size cap administration.

Admins (with MFA step-up) set ``max_bytes``; readers can view the
current policy so the dashboard surfaces it alongside other workspace
limits. Every mutation is appended to the audit log with before/after
state. Tenant scoped on every call; isolation is enforced by
``current_tenant_id``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import body_size
from ..audit import write_event
from ..auth import require_admin_with_mfa, require_roles
from ..tenant import current_tenant_id

router = APIRouter(tags=["body-size"], prefix="/body-size")


class BodySizeResponse(BaseModel):
    max_bytes: int = Field(
        ..., description=(
            "Maximum request body the API will accept on chargeable"
            " routes for this workspace. 0 means no cap (default)."
        )
    )
    ceiling: int = Field(
        ..., description=(
            "Hard upper bound the API will accept for max_bytes."
        )
    )
    updated_at: float = 0.0
    updated_by: str = ""


class BodySizeUpdate(BaseModel):
    max_bytes: int = Field(
        ..., ge=0,
        description=(
            "Maximum request body bytes. Set to 0 to disable the cap."
        ),
    )


def _actor_id(request: Request) -> str:
    return (
        getattr(request.state, "api_key_name", "")
        or getattr(request.state, "pat_id", "")
        or "unknown"
    )


def _to_response(request: Request) -> BodySizeResponse:
    tenant = current_tenant_id(request)
    pol = body_size.get_policy(tenant)
    return BodySizeResponse(
        max_bytes=pol.max_bytes,
        ceiling=body_size.MAX_BYTES_CEILING,
        updated_at=pol.updated_at,
        updated_by=pol.updated_by,
    )


@router.get(
    "",
    response_model=BodySizeResponse,
    dependencies=[Depends(require_roles("reader"))],
)
async def get_body_size(request: Request) -> BodySizeResponse:
    return _to_response(request)


@router.put(
    "",
    response_model=BodySizeResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def set_body_size(
    body: BodySizeUpdate, request: Request
) -> BodySizeResponse:
    tenant = current_tenant_id(request)
    actor = _actor_id(request)
    before = body_size.get_policy(tenant)
    try:
        saved = body_size.set_policy(
            tenant_id=tenant,
            max_bytes=body.max_bytes,
            updated_by=actor,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail={
            "code": "body_size_invalid",
            "message": str(e),
        })
    write_event(
        {
            "ts": saved.updated_at,
            "actor": actor,
            "tenant_id": tenant,
            "action": "body_size.update",
            "target": tenant,
            "request_id": getattr(request.state, "request_id", ""),
            "before": before.to_dict(),
            "after": saved.to_dict(),
        }
    )
    return _to_response(request)
