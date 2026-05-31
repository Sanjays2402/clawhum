"""Data Subject Access Request (DSAR) intake and tracking endpoints.

Admin-only CRUD over the tenant's DSAR queue. Every mutation is
tenant-scoped so an admin in tenant A cannot list, create, or
advance requests belonging to tenant B. Mutations require MFA and
the audit middleware records them so customers have a defensible
log when a regulator asks "how did you handle the request from
jane@example.com filed on 2025-01-15?"

Destructive transitions (rejecting a request, or hard-deleting one)
support ``?dry_run=true`` so privacy ops can preview what their
playbook will do before they run it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..auth import require_mfa, require_roles
from ..tenant import current_tenant
from .. import dsar

router = APIRouter(tags=["dsar"], prefix="/dsar")


class EventOut(BaseModel):
    at: float
    actor: str
    action: str
    note: str
    from_status: str
    to_status: str


class RequestOut(BaseModel):
    id: str
    subject_email: str
    kind: str
    status: str
    note: str
    created_at: float
    due_at: float
    updated_at: float
    closed_at: float
    overdue: bool
    history: list[EventOut]


class RequestListResponse(BaseModel):
    requests: list[RequestOut]
    summary: dict
    kinds: list[str]
    statuses: list[str]


class RequestCreate(BaseModel):
    subject_email: str = Field(min_length=3, max_length=254)
    kind: str = Field(default="access", max_length=32)
    note: str = Field(default="", max_length=2000)
    due_days: int = Field(default=dsar.DEFAULT_DUE_DAYS, ge=1, le=365)


class RequestAdvance(BaseModel):
    to_status: str = Field(min_length=1, max_length=32)
    note: str = Field(default="", max_length=2000)


def _actor(request: Request) -> str:
    name = getattr(request.state, "api_key_name", "") or "admin"
    return str(name)[:120]


def _to_out(r: dsar.Request) -> RequestOut:
    data = r.to_dict()
    return RequestOut(
        id=data["id"],
        subject_email=data["subject_email"],
        kind=data["kind"],
        status=data["status"],
        note=data["note"],
        created_at=data["created_at"],
        due_at=data["due_at"],
        updated_at=data["updated_at"],
        closed_at=data["closed_at"],
        overdue=data["overdue"],
        history=[EventOut(**e) for e in data["history"]],
    )


@router.get(
    "",
    response_model=RequestListResponse,
    dependencies=[Depends(require_roles("admin"))],
)
async def list_requests(
    status: str | None = None,
    tenant_id: str = Depends(current_tenant),
) -> RequestListResponse:
    if status is not None and status not in dsar.ALLOWED_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="invalid status filter; must be one of "
            + ", ".join(dsar.ALLOWED_STATUSES),
        )
    rows = dsar.list_requests(tenant_id, status=status)
    return RequestListResponse(
        requests=[_to_out(r) for r in rows],
        summary=dsar.summary(tenant_id),
        kinds=list(dsar.ALLOWED_KINDS),
        statuses=list(dsar.ALLOWED_STATUSES),
    )


@router.post(
    "",
    response_model=RequestOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def create_request(
    body: RequestCreate,
    request: Request,
    tenant_id: str = Depends(current_tenant),
):
    from ..dry_run import is_dry_run, preview
    if is_dry_run(request):
        return JSONResponse(preview(
            "dsar_request",
            None,
            tenant_id=tenant_id,
            subject_email=body.subject_email,
            kind=body.kind,
            due_days=body.due_days,
        ))
    try:
        row = dsar.file_request(
            tenant_id,
            subject_email=body.subject_email,
            kind=body.kind,
            note=body.note,
            actor=_actor(request),
            due_days=body.due_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_out(row)


@router.get(
    "/{request_id}",
    response_model=RequestOut,
    dependencies=[Depends(require_roles("admin"))],
)
async def get_request(
    request_id: str,
    tenant_id: str = Depends(current_tenant),
) -> RequestOut:
    row = dsar.get_request(tenant_id, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="request not found")
    return _to_out(row)


@router.post(
    "/{request_id}/advance",
    response_model=RequestOut,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def advance_request(
    request_id: str,
    body: RequestAdvance,
    request: Request,
    tenant_id: str = Depends(current_tenant),
):
    existing = dsar.get_request(tenant_id, request_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="request not found")
    from ..dry_run import is_dry_run, preview
    if is_dry_run(request):
        return JSONResponse(preview(
            "dsar_advance",
            request_id,
            tenant_id=tenant_id,
            from_status=existing.status,
            to_status=body.to_status,
        ))
    try:
        row = dsar.advance_request(
            tenant_id,
            request_id,
            to_status=body.to_status,
            note=body.note,
            actor=_actor(request),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="request not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _to_out(row)
