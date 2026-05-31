"""Workspace Data Processing Agreement (DPA) acceptance endpoints.

GET  /dpa            — readable by any seat. Shows the current DPA
                       version published by the vendor and whether the
                       caller's workspace has accepted it (and if so,
                       who and when).
POST /dpa/accept     — admin + fresh MFA. Records that the workspace
                       has accepted the current DPA version. Body
                       must echo the published ``version`` string so
                       a stale client cannot accidentally bind the
                       workspace to an outdated contract.
DELETE /dpa          — admin + fresh MFA. Withdraws the acceptance.
                       The audit chain retains the prior event.

Every mutation flows through AuditLogMiddleware (registered in
``app.create_app``) which captures method, path, tenant, actor, IP,
and request id in the tamper-evident audit chain. There is no extra
``write_event`` call here; doing it twice would forge a double entry.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .. import dpa
from ..auth import require_admin_with_mfa, require_roles
from ..dry_run import is_dry_run, preview
from ..tenant import current_tenant_id

router = APIRouter(tags=["dpa"], prefix="/dpa")


class AcceptanceOut(BaseModel):
    version: str
    accepted_by: str
    accepted_at: float
    ip: str
    user_agent: str


class DPAStatusResponse(BaseModel):
    current_version: str
    current_url: str
    accepted: bool
    acceptance: AcceptanceOut | None = None


class AcceptBody(BaseModel):
    # Client must echo the version it is accepting. This is the
    # contract under U.S. e-signature law (E-SIGN Act) and under
    # GDPR Art. 7 record-of-consent: the system must prove that the
    # specific document version the signer saw is the one they bound
    # the workspace to.
    version: str = Field(min_length=1, max_length=64)


def _to_out(a: dpa.Acceptance) -> AcceptanceOut:
    return AcceptanceOut(
        version=a.version,
        accepted_by=a.accepted_by,
        accepted_at=a.accepted_at,
        ip=a.ip,
        user_agent=a.user_agent,
    )


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    if request.client is not None:
        return request.client.host or ""
    return ""


@router.get(
    "",
    response_model=DPAStatusResponse,
    dependencies=[Depends(require_roles("reader"))],
)
async def get_dpa_status(request: Request) -> DPAStatusResponse:
    tenant_id = current_tenant_id(request)
    row = dpa.get_acceptance(tenant_id)
    return DPAStatusResponse(
        current_version=dpa.CURRENT_DPA_VERSION,
        current_url=dpa.CURRENT_DPA_URL,
        accepted=bool(row and row.version == dpa.CURRENT_DPA_VERSION),
        acceptance=_to_out(row) if row else None,
    )


@router.post(
    "/accept",
    response_model=AcceptanceOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def accept_dpa(request: Request, body: AcceptBody) -> AcceptanceOut:
    tenant_id = current_tenant_id(request)
    actor = getattr(request.state, "api_key_name", "") or ""
    if is_dry_run(request):
        return JSONResponse(
            preview(
                "dpa_accept",
                tenant_id,
                version=body.version,
                accepted_by=actor,
            )
        )
    try:
        row = dpa.accept(
            tenant_id,
            version=body.version,
            accepted_by=actor,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:512],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "dpa_version_mismatch", "message": str(exc)},
        ) from exc
    return _to_out(row)


@router.delete(
    "",
    dependencies=[Depends(require_admin_with_mfa())],
)
async def withdraw_dpa(request: Request):
    tenant_id = current_tenant_id(request)
    if is_dry_run(request):
        return JSONResponse(preview("dpa_withdraw", tenant_id))
    if not dpa.withdraw(tenant_id):
        raise HTTPException(
            status_code=404,
            detail={"error": "dpa_not_accepted"},
        )
    return Response(status_code=204)
