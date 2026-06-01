"""Per-workspace trusted reverse proxy administration.

Admin-only CRUD over the CIDR list the API trusts to set
``X-Forwarded-For`` for this workspace. Layers on top of the
deployment global ``CLAWHUM_TRUSTED_PROXIES_GLOBAL`` env so SecOps
keeps the ingress entry under operator control while still letting a
tenant add their own VPN gateway when they self host. Mutations are
admin-plus-MFA and run through the audit middleware. Reads expose
the operator's effective configuration plus a "what the API thinks
you are" diagnostic so SecOps can confirm their proxy is wired
correctly without leaving the settings page.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ..auth import require_mfa, require_roles
from ..tenant import current_tenant
from ..trusted_proxies import (
    ProxyRule,
    add_rule as _tp_add_rule,
    delete_rule as _tp_delete_rule,
    global_cidrs as _tp_global_cidrs,
    list_rules as _tp_list_rules,
    _is_trusted as _tp_is_trusted,
)
from ..ip_allowlist import client_ip_from_request as _client_ip_from_request


router = APIRouter(tags=["trusted-proxies"], prefix="/trusted-proxies")


class ProxyRuleOut(BaseModel):
    id: str
    cidr: str
    label: str
    created_at: float


class ProxyListResponse(BaseModel):
    workspace_rules: list[ProxyRuleOut]
    global_cidrs: list[str]
    your_socket_peer: str
    your_resolved_ip: str
    peer_is_trusted: bool


class ProxyCreate(BaseModel):
    cidr: str = Field(min_length=1, max_length=64)
    label: str = Field(default="", max_length=120)


def _row_to_out(r: ProxyRule) -> ProxyRuleOut:
    return ProxyRuleOut(id=r.id, cidr=r.cidr, label=r.label, created_at=r.created_at)


@router.get(
    "",
    response_model=ProxyListResponse,
    dependencies=[Depends(require_roles("admin"))],
)
async def list_proxies(request: Request, tenant_id: str = Depends(current_tenant)) -> ProxyListResponse:
    rules = _tp_list_rules(tenant_id)
    peer = request.client.host if request.client else ""
    headers = {k.decode().lower(): v.decode() for k, v in request.headers.raw}
    resolved = _client_ip_from_request(headers, peer, tenant_id=tenant_id)
    return ProxyListResponse(
        workspace_rules=[_row_to_out(r) for r in rules],
        global_cidrs=_tp_global_cidrs(),
        your_socket_peer=peer or "",
        your_resolved_ip=resolved,
        peer_is_trusted=_tp_is_trusted(peer or "", tenant_id),
    )


@router.post(
    "",
    response_model=ProxyRuleOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def create_proxy(body: ProxyCreate, tenant_id: str = Depends(current_tenant)) -> ProxyRuleOut:
    try:
        rule = _tp_add_rule(tenant_id, body.cidr, body.label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid cidr: {exc}") from exc
    return _row_to_out(rule)


@router.delete(
    "/{rule_id}",
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def delete_proxy(rule_id: str, request: Request, tenant_id: str = Depends(current_tenant)):
    from ..dry_run import is_dry_run, preview
    existing = next((r for r in _tp_list_rules(tenant_id) if r.id == rule_id), None)
    if existing is None:
        raise HTTPException(status_code=404, detail="rule not found")
    if is_dry_run(request):
        return JSONResponse(preview(
            "trusted_proxy_rule", rule_id,
            tenant_id=tenant_id, cidr=existing.cidr, label=existing.label,
        ))
    if not _tp_delete_rule(tenant_id, rule_id):
        raise HTTPException(status_code=404, detail="rule not found")
    return Response(status_code=204)
