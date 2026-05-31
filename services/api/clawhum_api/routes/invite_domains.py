"""Workspace invite email-domain allowlist administration.

Admin-only CRUD over the per-tenant invite domain list. Every mutation
is tenant-scoped: an admin in tenant A cannot list, add, or remove
domains belonging to tenant B. The same store gates new invites, SSO
auto-join, and SCIM-side provisioning so a single policy applies to
every path that grants a workspace seat.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .. import invite_domains
from ..auth import require_admin_with_mfa, require_roles
from ..dry_run import is_dry_run, preview
from ..tenant import current_tenant_id

router = APIRouter(tags=["invite-domains"], prefix="/invite-domains")


class DomainOut(BaseModel):
    id: str
    domain: str
    include_subdomains: bool
    label: str
    created_at: float


class DomainListResponse(BaseModel):
    enforcing: bool
    domains: list[DomainOut]


class DomainCreate(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    include_subdomains: bool = False
    label: str = Field(default="", max_length=120)


def _to_out(d: invite_domains.Domain) -> DomainOut:
    return DomainOut(
        id=d.id,
        domain=d.domain,
        include_subdomains=d.include_subdomains,
        label=d.label,
        created_at=d.created_at,
    )


@router.get(
    "",
    response_model=DomainListResponse,
    dependencies=[Depends(require_roles("reader"))],
)
async def list_invite_domains(request: Request) -> DomainListResponse:
    tenant_id = current_tenant_id(request)
    rows = invite_domains.list_domains(tenant_id)
    return DomainListResponse(
        enforcing=bool(rows),
        domains=[_to_out(r) for r in rows],
    )


@router.post(
    "",
    response_model=DomainOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def create_invite_domain(request: Request, body: DomainCreate) -> DomainOut:
    tenant_id = current_tenant_id(request)
    try:
        row = invite_domains.add_domain(
            tenant_id,
            body.domain,
            include_subdomains=body.include_subdomains,
            label=body.label,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_out(row)


@router.delete(
    "/{domain_id}",
    dependencies=[Depends(require_admin_with_mfa())],
)
async def delete_invite_domain(domain_id: str, request: Request):
    tenant_id = current_tenant_id(request)
    existing = next(
        (d for d in invite_domains.list_domains(tenant_id) if d.id == domain_id),
        None,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="domain not found")
    if is_dry_run(request):
        return JSONResponse(
            preview(
                "invite_domain",
                domain_id,
                tenant_id=tenant_id,
                domain=existing.domain,
                include_subdomains=existing.include_subdomains,
                label=existing.label,
            )
        )
    if not invite_domains.delete_domain(tenant_id, domain_id):
        raise HTTPException(status_code=404, detail="domain not found")
    return Response(status_code=204)
