"""Per-workspace security and breach notification contact administration.

Admin-only CRUD over the tenant's security contact roster. Every
mutation is tenant-scoped so an admin in tenant A cannot list, add,
delete, or promote contacts belonging to tenant B. The audit
middleware records every mutating call so customers have an evidence
trail proving who changed their incident contact list and when, which
is what auditors actually ask for during SOC2 and ISO 27001 reviews.

Mutations also require MFA, matching the rest of the security
sensitive admin surface (IP allowlist, embed origins, SSO).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ..auth import require_mfa, require_roles
from ..tenant import current_tenant
from .. import security_contacts

router = APIRouter(tags=["security-contacts"], prefix="/security-contacts")


class ContactOut(BaseModel):
    id: str
    email: str
    name: str
    role: str
    phone: str
    primary: bool
    created_at: float


class ContactListResponse(BaseModel):
    contacts: list[ContactOut]
    primary_id: str | None
    roles: list[str]


class ContactCreate(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    name: str = Field(default="", max_length=120)
    role: str = Field(default="security", max_length=32)
    phone: str = Field(default="", max_length=64)
    primary: bool = False


def _to_out(c: security_contacts.Contact) -> ContactOut:
    return ContactOut(
        id=c.id,
        email=c.email,
        name=c.name,
        role=c.role,
        phone=c.phone,
        primary=c.primary,
        created_at=c.created_at,
    )


@router.get(
    "",
    response_model=ContactListResponse,
    dependencies=[Depends(require_roles("admin"))],
)
async def list_contacts(tenant_id: str = Depends(current_tenant)) -> ContactListResponse:
    rows = security_contacts.list_contacts(tenant_id)
    primary = next((r.id for r in rows if r.primary), None)
    return ContactListResponse(
        contacts=[_to_out(r) for r in rows],
        primary_id=primary,
        roles=list(security_contacts.ALLOWED_ROLES),
    )


@router.post(
    "",
    response_model=ContactOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def create_contact(
    body: ContactCreate,
    tenant_id: str = Depends(current_tenant),
) -> ContactOut:
    try:
        row = security_contacts.add_contact(
            tenant_id,
            email=body.email,
            name=body.name,
            role=body.role,
            phone=body.phone,
            primary=body.primary,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_out(row)


@router.post(
    "/{contact_id}/primary",
    response_model=ContactOut,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def make_primary(
    contact_id: str,
    request: Request,
    tenant_id: str = Depends(current_tenant),
) -> ContactOut:
    from ..dry_run import is_dry_run, preview
    existing = next(
        (c for c in security_contacts.list_contacts(tenant_id) if c.id == contact_id),
        None,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="contact not found")
    if is_dry_run(request):
        return JSONResponse(preview(
            "security_contact_primary",
            contact_id,
            tenant_id=tenant_id,
            email=existing.email,
        ))
    security_contacts.promote_primary(tenant_id, contact_id)
    updated = next(
        c for c in security_contacts.list_contacts(tenant_id) if c.id == contact_id
    )
    return _to_out(updated)


@router.delete(
    "/{contact_id}",
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def delete_contact(
    contact_id: str,
    request: Request,
    tenant_id: str = Depends(current_tenant),
):
    from ..dry_run import is_dry_run, preview
    existing = next(
        (c for c in security_contacts.list_contacts(tenant_id) if c.id == contact_id),
        None,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="contact not found")
    if is_dry_run(request):
        return JSONResponse(preview(
            "security_contact",
            contact_id,
            tenant_id=tenant_id,
            email=existing.email,
            role=existing.role,
        ))
    if not security_contacts.delete_contact(tenant_id, contact_id):
        raise HTTPException(status_code=404, detail="contact not found")
    return Response(status_code=204)
