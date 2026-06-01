"""Security incident (breach notification) endpoints.

Admin-only CRUD over the tenant's incident queue. Every mutation is
tenant-scoped so an admin in tenant A cannot list, declare, advance,
or update incidents belonging to tenant B. Mutations require MFA and
the audit middleware records them so customers can demonstrate a
defensible response to a regulator follow-up.

The endpoints exist to make GDPR Art 33 (72h regulator notification)
and Art 34 (data subject notification) auditable, plus to satisfy
SOC2 CC7.3 incident evaluation. The schema is deliberately small so
it fits on one settings page and does not turn into a half-built
ticketing system.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..auth import require_mfa, require_roles
from ..tenant import current_tenant
from .. import incidents


router = APIRouter(tags=["incidents"], prefix="/incidents")


class EventOut(BaseModel):
    at: float
    actor: str
    kind: str
    note: str
    from_status: str
    to_status: str


class IncidentOut(BaseModel):
    id: str
    title: str
    severity: str
    status: str
    detail: str
    discovered_at: float
    created_at: float
    updated_at: float
    closed_at: float
    regulator_notified_at: float
    regulator_name: str
    regulator_reference: str
    subjects_notified_at: float
    affected_count: int
    notify_deadline_at: float
    notify_overdue: bool
    history: list[EventOut]


class IncidentListResponse(BaseModel):
    incidents: list[IncidentOut]
    summary: dict
    severities: list[str]
    statuses: list[str]
    notify_deadline_seconds: int


class IncidentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    severity: str = Field(default="medium", max_length=32)
    detail: str = Field(default="", max_length=8000)
    discovered_at: float | None = Field(default=None, ge=0)


class IncidentAdvance(BaseModel):
    to_status: str = Field(min_length=1, max_length=32)
    note: str = Field(default="", max_length=4000)


class IncidentNote(BaseModel):
    note: str = Field(min_length=1, max_length=4000)


class RegulatorNotify(BaseModel):
    regulator_name: str = Field(min_length=1, max_length=200)
    regulator_reference: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=4000)


class SubjectsNotify(BaseModel):
    affected_count: int = Field(ge=0, le=10_000_000)
    note: str = Field(default="", max_length=4000)


def _actor(request: Request) -> str:
    name = getattr(request.state, "api_key_name", "") or "admin"
    return str(name)[:120]


def _to_out(inc: incidents.Incident) -> IncidentOut:
    data = inc.to_dict()
    return IncidentOut(
        id=data["id"],
        title=data["title"],
        severity=data["severity"],
        status=data["status"],
        detail=data["detail"],
        discovered_at=data["discovered_at"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        closed_at=data["closed_at"],
        regulator_notified_at=data["regulator_notified_at"],
        regulator_name=data["regulator_name"],
        regulator_reference=data["regulator_reference"],
        subjects_notified_at=data["subjects_notified_at"],
        affected_count=data["affected_count"],
        notify_deadline_at=data["notify_deadline_at"],
        notify_overdue=data["notify_overdue"],
        history=[EventOut(**e) for e in data["history"]],
    )


@router.get(
    "",
    response_model=IncidentListResponse,
    dependencies=[Depends(require_roles("admin"))],
)
async def list_incidents_endpoint(
    status: str | None = None,
    tenant_id: str = Depends(current_tenant),
) -> IncidentListResponse:
    if status is not None and status not in incidents.ALLOWED_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="invalid status filter; must be one of "
            + ", ".join(incidents.ALLOWED_STATUSES),
        )
    rows = incidents.list_incidents(tenant_id, status=status)
    return IncidentListResponse(
        incidents=[_to_out(r) for r in rows],
        summary=incidents.summary(tenant_id),
        severities=list(incidents.ALLOWED_SEVERITIES),
        statuses=list(incidents.ALLOWED_STATUSES),
        notify_deadline_seconds=incidents.NOTIFY_DEADLINE_SECONDS,
    )


@router.post(
    "",
    response_model=IncidentOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def declare_incident_endpoint(
    body: IncidentCreate,
    request: Request,
    tenant_id: str = Depends(current_tenant),
):
    from ..dry_run import is_dry_run, preview
    if is_dry_run(request):
        return JSONResponse(preview(
            "incident_declare",
            None,
            tenant_id=tenant_id,
            title=body.title,
            severity=body.severity,
        ))
    try:
        row = incidents.declare_incident(
            tenant_id,
            title=body.title,
            severity=body.severity,
            detail=body.detail,
            discovered_at=body.discovered_at,
            actor=_actor(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_out(row)


@router.get(
    "/{incident_id}",
    response_model=IncidentOut,
    dependencies=[Depends(require_roles("admin"))],
)
async def get_incident_endpoint(
    incident_id: str,
    tenant_id: str = Depends(current_tenant),
) -> IncidentOut:
    row = incidents.get_incident(tenant_id, incident_id)
    if row is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return _to_out(row)


@router.post(
    "/{incident_id}/notes",
    response_model=IncidentOut,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def append_note_endpoint(
    incident_id: str,
    body: IncidentNote,
    request: Request,
    tenant_id: str = Depends(current_tenant),
):
    try:
        row = incidents.append_note(
            tenant_id,
            incident_id,
            note=body.note,
            actor=_actor(request),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="incident not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _to_out(row)


@router.post(
    "/{incident_id}/advance",
    response_model=IncidentOut,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def advance_incident_endpoint(
    incident_id: str,
    body: IncidentAdvance,
    request: Request,
    tenant_id: str = Depends(current_tenant),
):
    existing = incidents.get_incident(tenant_id, incident_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="incident not found")
    from ..dry_run import is_dry_run, preview
    if is_dry_run(request):
        return JSONResponse(preview(
            "incident_advance",
            incident_id,
            tenant_id=tenant_id,
            from_status=existing.status,
            to_status=body.to_status,
        ))
    try:
        row = incidents.advance_incident(
            tenant_id,
            incident_id,
            to_status=body.to_status,
            note=body.note,
            actor=_actor(request),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="incident not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _to_out(row)


@router.post(
    "/{incident_id}/regulator-notified",
    response_model=IncidentOut,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def regulator_notified_endpoint(
    incident_id: str,
    body: RegulatorNotify,
    request: Request,
    tenant_id: str = Depends(current_tenant),
):
    try:
        row = incidents.record_regulator_notification(
            tenant_id,
            incident_id,
            regulator_name=body.regulator_name,
            regulator_reference=body.regulator_reference,
            note=body.note,
            actor=_actor(request),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="incident not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _to_out(row)


@router.post(
    "/{incident_id}/subjects-notified",
    response_model=IncidentOut,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def subjects_notified_endpoint(
    incident_id: str,
    body: SubjectsNotify,
    request: Request,
    tenant_id: str = Depends(current_tenant),
):
    try:
        row = incidents.record_subjects_notification(
            tenant_id,
            incident_id,
            affected_count=body.affected_count,
            note=body.note,
            actor=_actor(request),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="incident not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _to_out(row)
