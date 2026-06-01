"""Per-workspace system-use notification administration and ack API.

GET ``/system-use-notification``
    Reader-scoped. Returns the current banner record plus whether the
    *calling actor* still owes an acknowledgement. Clients render the
    banner from this payload so a fresh integration sees the gate
    immediately.

POST ``/system-use-notification/ack``
    Any authenticated actor. Records the actor's acknowledgement of
    the current revision. Returns 409 ``revision_mismatch`` if the
    client acks a stale revision so the UI can re-prompt with the
    fresh wording.

PUT ``/system-use-notification``
    Admin + MFA. Upserts the banner. Revision bumps when title or
    body changes, invalidating every prior ack and forcing a
    re-acknowledgement campaign across the workspace.

GET ``/system-use-notification/acks``
    Admin. Returns the latest ack per actor for an evidence pack.

Enforcement of the banner on mutating routes lives in
``SystemUseNotificationMiddleware``; this module only owns the
admin surface and the ack mutation itself.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import system_use_notification as sun
from ..audit import write_event
from ..auth import require_admin_with_mfa, require_roles
from ..tenant import current_tenant_id

router = APIRouter(
    tags=["system-use-notification"], prefix="/system-use-notification"
)


def _actor_id(request: Request) -> str:
    return (
        getattr(request.state, "api_key_name", "")
        or getattr(request.state, "pat_id", "")
        or "unknown"
    )


def _client_ip(request: Request) -> str:
    xff = (
        getattr(request.state, "client_ip", "")
        or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "")
    )
    return xff or ""


class BannerResponse(BaseModel):
    enforced: bool
    revision: int
    title: str
    body: str
    updated_at: float = 0.0
    updated_by: str = ""
    needs_ack: bool
    actor_id: str
    actor_acked_revision: int


class BannerUpdate(BaseModel):
    title: str = Field(default="", max_length=sun.MAX_TITLE_LEN)
    body: str = Field(default="", max_length=sun.MAX_BODY_LEN)
    enforced: bool = True


class AckRequest(BaseModel):
    revision: int = Field(ge=0)


class AckResponse(BaseModel):
    actor_id: str
    revision: int
    acked_at: float
    ip: str


class AckListEntry(BaseModel):
    actor_id: str
    revision: int
    acked_at: float
    ip: str


def _build_response(request: Request, tenant: str) -> BannerResponse:
    banner = sun.get_banner(tenant)
    actor = _actor_id(request)
    if banner is None:
        return BannerResponse(
            enforced=False,
            revision=0,
            title="",
            body="",
            updated_at=0.0,
            updated_by="",
            needs_ack=False,
            actor_id=actor,
            actor_acked_revision=sun.acked_revision(tenant, actor),
        )
    needs = sun.needs_ack(tenant, actor) is not None
    return BannerResponse(
        enforced=banner.enforced,
        revision=banner.revision,
        title=banner.title,
        body=banner.body,
        updated_at=banner.updated_at,
        updated_by=banner.updated_by,
        needs_ack=needs,
        actor_id=actor,
        actor_acked_revision=sun.acked_revision(tenant, actor),
    )


@router.get(
    "",
    response_model=BannerResponse,
    dependencies=[Depends(require_roles("reader", "writer"))],
)
async def get_banner(request: Request) -> BannerResponse:
    return _build_response(request, current_tenant_id(request))


@router.put(
    "",
    response_model=BannerResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def put_banner(body: BannerUpdate, request: Request) -> BannerResponse:
    tenant = current_tenant_id(request)
    actor = _actor_id(request)
    before = sun.get_banner(tenant)
    saved = sun.set_banner(
        tenant_id=tenant,
        title=body.title,
        body=body.body,
        enforced=body.enforced,
        updated_by=actor,
    )
    write_event(
        {
            "ts": saved.updated_at,
            "actor": actor,
            "tenant_id": tenant,
            "action": "system_use_notification.update",
            "target": tenant,
            "request_id": getattr(request.state, "request_id", ""),
            "before": before.to_dict() if before else {},
            "after": saved.to_dict(),
        }
    )
    return _build_response(request, tenant)


@router.post(
    "/ack",
    response_model=AckResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_roles("reader", "writer"))],
)
async def post_ack(body: AckRequest, request: Request) -> AckResponse:
    tenant = current_tenant_id(request)
    actor = _actor_id(request)
    banner = sun.get_banner(tenant)
    if banner is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no system use notification configured for this workspace",
        )
    if body.revision != banner.revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "revision_mismatch",
                "message": (
                    "current banner revision differs from the ack payload"
                ),
                "current_revision": banner.revision,
            },
        )
    ack = sun.record_ack(
        tenant_id=tenant,
        actor_id=actor,
        revision=banner.revision,
        ip=_client_ip(request),
    )
    write_event(
        {
            "ts": ack.acked_at,
            "actor": actor,
            "tenant_id": tenant,
            "action": "system_use_notification.ack",
            "target": f"rev:{banner.revision}",
            "request_id": getattr(request.state, "request_id", ""),
            "before": {},
            "after": ack.to_dict(),
        }
    )
    return AckResponse(**ack.to_dict())


@router.get(
    "/acks",
    response_model=list[AckListEntry],
    dependencies=[Depends(require_roles("admin"))],
)
async def get_acks(request: Request) -> list[AckListEntry]:
    tenant = current_tenant_id(request)
    rows = sun.list_acks(tenant)
    return [
        AckListEntry(
            actor_id=str(r.get("actor_id") or ""),
            revision=int(r.get("revision") or 0),
            acked_at=float(r.get("acked_at") or 0.0),
            ip=str(r.get("ip") or ""),
        )
        for r in rows
    ]
