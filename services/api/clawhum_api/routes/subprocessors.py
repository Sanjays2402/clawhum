"""Sub-processor registry routes: public read, platform-admin write,
per-workspace acknowledgement and change notification subscriptions.

* ``GET  /subprocessors``                                — any authed role
* ``POST /subprocessors``                                — platform admin
* ``PATCH /subprocessors/{id}``                          — platform admin
* ``DELETE /subprocessors/{id}``                         — platform admin
* ``GET  /subprocessors/acknowledgement``                — admin role
* ``POST /subprocessors/acknowledgement``                — admin + MFA
* ``GET  /subprocessors/subscriptions``                  — admin role
* ``POST /subprocessors/subscriptions``                  — admin + MFA
* ``DELETE /subprocessors/subscriptions/{id}``           — admin + MFA

Every mutating call passes through ``AuditLogMiddleware`` so the change
to a Article 28(2) sub-processor list, or a workspace acknowledgement,
becomes part of the immutable audit trail customers export during
SOC2 / ISO 27001 evidence collection.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from clawhum_core.settings import get_settings

from ..auth import require_mfa, require_roles
from ..tenant import current_tenant
from .. import subprocessors

router = APIRouter(tags=["subprocessors"], prefix="/subprocessors")


# Schemas ---------------------------------------------------------------


class ProcessorOut(BaseModel):
    id: str
    name: str
    purpose: str
    region: str
    data_categories: list[str]
    dpa_url: str
    status: str
    created_at: float
    updated_at: float


class RegistryResponse(BaseModel):
    revision: int
    processors: list[ProcessorOut]
    statuses: list[str]
    can_manage: bool


class ProcessorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    purpose: str = Field(default="", max_length=240)
    region: str = Field(default="", max_length=64)
    data_categories: list[str] = Field(default_factory=list)
    dpa_url: str = Field(default="", max_length=512)
    status: str = Field(default="active", max_length=16)


class ProcessorUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    purpose: str | None = Field(default=None, max_length=240)
    region: str | None = Field(default=None, max_length=64)
    data_categories: list[str] | None = None
    dpa_url: str | None = Field(default=None, max_length=512)
    status: str | None = Field(default=None, max_length=16)


class AcknowledgementOut(BaseModel):
    tenant_id: str
    revision: int
    acknowledged_by: str
    acknowledged_at: float
    current_revision: int
    up_to_date: bool


class AcknowledgementCreate(BaseModel):
    revision: int = Field(ge=0)


class SubscriptionOut(BaseModel):
    id: str
    email: str
    created_at: float


class SubscriptionListResponse(BaseModel):
    subscriptions: list[SubscriptionOut]


class SubscriptionCreate(BaseModel):
    email: str = Field(min_length=3, max_length=254)


# Helpers ---------------------------------------------------------------


def _to_out(sp: subprocessors.SubProcessor) -> ProcessorOut:
    return ProcessorOut(
        id=sp.id,
        name=sp.name,
        purpose=sp.purpose,
        region=sp.region,
        data_categories=list(sp.data_categories),
        dpa_url=sp.dpa_url,
        status=sp.status,
        created_at=sp.created_at,
        updated_at=sp.updated_at,
    )


def _platform_admin_tenants() -> set[str]:
    raw = get_settings().subprocessors_platform_admin_tenants or ""
    return {t.strip() for t in raw.split(",") if t.strip()}


def _is_platform_admin(tenant_id: str) -> bool:
    allow = _platform_admin_tenants()
    return tenant_id in allow if allow else False


def _require_platform_admin(tenant_id: str = Depends(current_tenant)) -> str:
    if not _is_platform_admin(tenant_id):
        raise HTTPException(
            status_code=403,
            detail="workspace is not authorised to mutate the sub-processor registry",
        )
    return tenant_id


def _actor(request: Request) -> str:
    actor = getattr(request.state, "api_key_name", None) or ""
    if isinstance(actor, str):
        return actor
    return str(actor)


# Public read -----------------------------------------------------------


@router.get(
    "",
    response_model=RegistryResponse,
    dependencies=[Depends(require_roles("reader", "writer", "admin"))],
)
async def list_registry(
    include_removed: bool = False,
    tenant_id: str = Depends(current_tenant),
) -> RegistryResponse:
    rows = subprocessors.list_processors(include_removed=include_removed)
    return RegistryResponse(
        revision=subprocessors.current_revision(),
        processors=[_to_out(r) for r in rows],
        statuses=list(subprocessors.ALLOWED_STATUS),
        can_manage=_is_platform_admin(tenant_id),
    )


# Platform admin write -------------------------------------------------


@router.post(
    "",
    response_model=ProcessorOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def create_processor(
    body: ProcessorCreate,
    tenant_id: str = Depends(_require_platform_admin),
) -> ProcessorOut:
    try:
        sp = subprocessors.add_processor(
            name=body.name,
            purpose=body.purpose,
            region=body.region,
            data_categories=body.data_categories,
            dpa_url=body.dpa_url,
            status=body.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_out(sp)


@router.patch(
    "/{processor_id}",
    response_model=ProcessorOut,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def update_processor(
    processor_id: str,
    body: ProcessorUpdate,
    tenant_id: str = Depends(_require_platform_admin),
) -> ProcessorOut:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        sp = subprocessors.update_processor(processor_id, **fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if sp is None:
        raise HTTPException(status_code=404, detail="sub-processor not found")
    return _to_out(sp)


@router.delete(
    "/{processor_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def delete_processor(
    processor_id: str,
    tenant_id: str = Depends(_require_platform_admin),
):
    ok = subprocessors.delete_processor(processor_id)
    if not ok:
        raise HTTPException(status_code=404, detail="sub-processor not found")
    return None


# Per-workspace acknowledgement ----------------------------------------


@router.get(
    "/acknowledgement",
    response_model=AcknowledgementOut,
    dependencies=[Depends(require_roles("admin"))],
)
async def get_acknowledgement(
    tenant_id: str = Depends(current_tenant),
) -> AcknowledgementOut:
    current = subprocessors.current_revision()
    ack = subprocessors.get_acknowledgement(tenant_id)
    if ack is None:
        return AcknowledgementOut(
            tenant_id=tenant_id,
            revision=0,
            acknowledged_by="",
            acknowledged_at=0.0,
            current_revision=current,
            up_to_date=current == 0,
        )
    return AcknowledgementOut(
        tenant_id=ack.tenant_id,
        revision=ack.revision,
        acknowledged_by=ack.acknowledged_by,
        acknowledged_at=ack.acknowledged_at,
        current_revision=current,
        up_to_date=ack.revision >= current,
    )


@router.post(
    "/acknowledgement",
    response_model=AcknowledgementOut,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def post_acknowledgement(
    body: AcknowledgementCreate,
    request: Request,
    tenant_id: str = Depends(current_tenant),
) -> AcknowledgementOut:
    current = subprocessors.current_revision()
    if body.revision != current:
        raise HTTPException(
            status_code=409,
            detail=(
                "acknowledgement revision does not match current registry "
                f"revision {current}; refresh and retry"
            ),
        )
    ack = subprocessors.acknowledge(tenant_id, _actor(request), current)
    return AcknowledgementOut(
        tenant_id=ack.tenant_id,
        revision=ack.revision,
        acknowledged_by=ack.acknowledged_by,
        acknowledged_at=ack.acknowledged_at,
        current_revision=current,
        up_to_date=True,
    )


# Per-workspace notification subscriptions -----------------------------


@router.get(
    "/subscriptions",
    response_model=SubscriptionListResponse,
    dependencies=[Depends(require_roles("admin"))],
)
async def list_subscriptions(
    tenant_id: str = Depends(current_tenant),
) -> SubscriptionListResponse:
    rows = subprocessors.list_subscriptions(tenant_id)
    return SubscriptionListResponse(
        subscriptions=[
            SubscriptionOut(id=s.id, email=s.email, created_at=s.created_at)
            for s in rows
        ]
    )


@router.post(
    "/subscriptions",
    response_model=SubscriptionOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def add_subscription(
    body: SubscriptionCreate,
    tenant_id: str = Depends(current_tenant),
) -> SubscriptionOut:
    try:
        sub = subprocessors.add_subscription(tenant_id, body.email)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SubscriptionOut(id=sub.id, email=sub.email, created_at=sub.created_at)


@router.delete(
    "/subscriptions/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def delete_subscription(
    subscription_id: str,
    tenant_id: str = Depends(current_tenant),
):
    ok = subprocessors.delete_subscription(tenant_id, subscription_id)
    if not ok:
        raise HTTPException(status_code=404, detail="subscription not found")
    return None
