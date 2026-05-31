"""Workspace embed origin allowlist administration.

Admin-only CRUD over the per-tenant embed origin list. Every mutation
is tenant-scoped: an admin in tenant A cannot list, add, or remove
origins belonging to tenant B. The same store gates which sites may
frame the public ``/r/{id}/embed`` page and which sites may invoke
``/api/oembed`` for shares owned by that tenant.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..auth import require_mfa, require_roles
from ..tenant import current_tenant
from .. import embed_origins

router = APIRouter(tags=["embed-origins"], prefix="/embed-origins")


class OriginOut(BaseModel):
    id: str
    origin: str
    label: str
    created_at: float


class OriginListResponse(BaseModel):
    enforcing: bool
    origins: list[OriginOut]


class OriginCreate(BaseModel):
    origin: str = Field(min_length=1, max_length=256)
    label: str = Field(default="", max_length=120)


def _row_to_out(o: embed_origins.Origin) -> OriginOut:
    return OriginOut(id=o.id, origin=o.origin, label=o.label, created_at=o.created_at)


@router.get(
    "",
    response_model=OriginListResponse,
    dependencies=[Depends(require_roles("admin"))],
)
async def list_origins(tenant_id: str = Depends(current_tenant)) -> OriginListResponse:
    rows = embed_origins.list_origins(tenant_id)
    return OriginListResponse(
        enforcing=bool(rows),
        origins=[_row_to_out(r) for r in rows],
    )


@router.post(
    "",
    response_model=OriginOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def create_origin(body: OriginCreate, tenant_id: str = Depends(current_tenant)) -> OriginOut:
    try:
        row = embed_origins.add_origin(tenant_id, body.origin, body.label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid origin: {exc}") from exc
    return _row_to_out(row)


@router.delete(
    "/{origin_id}",
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def delete_origin(origin_id: str, request: Request, tenant_id: str = Depends(current_tenant)):
    from ..dry_run import is_dry_run, preview
    from fastapi.responses import JSONResponse, Response

    existing = next((o for o in embed_origins.list_origins(tenant_id) if o.id == origin_id), None)
    if existing is None:
        raise HTTPException(status_code=404, detail="origin not found")
    if is_dry_run(request):
        return JSONResponse(preview(
            "embed_origin",
            origin_id,
            tenant_id=tenant_id,
            origin=existing.origin,
            label=existing.label,
        ))
    if not embed_origins.delete_origin(tenant_id, origin_id):
        raise HTTPException(status_code=404, detail="origin not found")
    return Response(status_code=204)
