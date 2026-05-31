"""Workspace IP allowlist administration.

Admin-only CRUD over the per-tenant CIDR rule list. Every mutation is
tenant-scoped, so a key from tenant A cannot list, add, or delete
rules belonging to tenant B even with admin role. The same enforcement
also gates the rest of the API via ``auth._enforce_ip_allowlist``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..auth import require_roles
from ..tenant import current_tenant
from .. import ip_allowlist

router = APIRouter(tags=["ip-allowlist"], prefix="/ip-allowlist")


class RuleOut(BaseModel):
    id: str
    cidr: str
    label: str
    created_at: float


class RuleListResponse(BaseModel):
    enforcing: bool
    rules: list[RuleOut]
    your_ip: str


class RuleCreate(BaseModel):
    cidr: str = Field(min_length=1, max_length=64)
    label: str = Field(default="", max_length=120)


def _row_to_out(r: ip_allowlist.Rule) -> RuleOut:
    return RuleOut(id=r.id, cidr=r.cidr, label=r.label, created_at=r.created_at)


def _client_ip(request: Request) -> str:
    headers = {k.decode().lower(): v.decode() for k, v in request.headers.raw}
    host = request.client.host if request.client else None
    return ip_allowlist.client_ip_from_request(headers, host)


@router.get(
    "",
    response_model=RuleListResponse,
    dependencies=[Depends(require_roles("admin"))],
)
async def list_rules(request: Request, tenant_id: str = Depends(current_tenant)) -> RuleListResponse:
    rules = ip_allowlist.list_rules(tenant_id)
    return RuleListResponse(
        enforcing=bool(rules),
        rules=[_row_to_out(r) for r in rules],
        your_ip=_client_ip(request),
    )


@router.post(
    "",
    response_model=RuleOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("admin"))],
)
async def create_rule(body: RuleCreate, tenant_id: str = Depends(current_tenant)) -> RuleOut:
    try:
        rule = ip_allowlist.add_rule(tenant_id, body.cidr, body.label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid cidr: {exc}") from exc
    return _row_to_out(rule)


@router.delete(
    "/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles("admin"))],
)
async def delete_rule(rule_id: str, tenant_id: str = Depends(current_tenant)) -> None:
    if not ip_allowlist.delete_rule(tenant_id, rule_id):
        # Treat unknown ids as not-found rather than leaking whether the
        # id exists in some other tenant.
        raise HTTPException(status_code=404, detail="rule not found")
    return None
