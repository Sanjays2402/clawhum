"""Per-workspace match result top-k cap administration.

Admins (with MFA step-up) set ``max_top_k``; readers can view the
current policy. Every mutation is appended to the audit log with
before/after state. Tenant scoped on every call; isolation is enforced
by ``current_tenant_id``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import match_topk
from ..audit import write_event
from ..auth import require_admin_with_mfa, require_roles
from ..tenant import current_tenant_id

router = APIRouter(tags=["match-topk"], prefix="/match-topk")


class MatchTopKResponse(BaseModel):
    max_top_k: int = Field(
        ..., description=(
            "Maximum value of top_k the /match and /batch routes will"
            " accept for this workspace. 0 means no cap (default)."
        )
    )
    ceiling: int = Field(
        ..., description=(
            "Hard upper bound the API will accept for max_top_k."
        )
    )
    updated_at: float = 0.0
    updated_by: str = ""


class MatchTopKUpdate(BaseModel):
    max_top_k: int = Field(
        ..., ge=0,
        description=(
            "Maximum value of top_k accepted from clients. Set to 0"
            " to disable the cap."
        ),
    )


def _actor_id(request: Request) -> str:
    return (
        getattr(request.state, "api_key_name", "")
        or getattr(request.state, "pat_id", "")
        or "unknown"
    )


def _to_response(request: Request) -> MatchTopKResponse:
    tenant = current_tenant_id(request)
    pol = match_topk.get_policy(tenant)
    return MatchTopKResponse(
        max_top_k=pol.max_top_k,
        ceiling=match_topk.MAX_TOP_K_CEILING,
        updated_at=pol.updated_at,
        updated_by=pol.updated_by,
    )


@router.get(
    "",
    response_model=MatchTopKResponse,
    dependencies=[Depends(require_roles("reader"))],
)
async def get_match_topk(request: Request) -> MatchTopKResponse:
    return _to_response(request)


@router.put(
    "",
    response_model=MatchTopKResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def set_match_topk(
    body: MatchTopKUpdate, request: Request
) -> MatchTopKResponse:
    tenant = current_tenant_id(request)
    actor = _actor_id(request)
    before = match_topk.get_policy(tenant)
    try:
        saved = match_topk.set_policy(
            tenant_id=tenant,
            max_top_k=body.max_top_k,
            updated_by=actor,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail={
            "code": "match_topk_invalid",
            "message": str(e),
        })
    write_event(
        {
            "ts": saved.updated_at,
            "actor": actor,
            "tenant_id": tenant,
            "action": "match_topk.update",
            "target": tenant,
            "request_id": getattr(request.state, "request_id", ""),
            "before": before.to_dict(),
            "after": saved.to_dict(),
        }
    )
    return _to_response(request)
