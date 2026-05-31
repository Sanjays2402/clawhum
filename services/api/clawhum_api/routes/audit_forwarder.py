"""Admin surface for per-workspace audit log forwarding.

Endpoints
- GET    /audit-forwarding             current destination + status
- PUT    /audit-forwarding             create or replace (rotates secret)
- POST   /audit-forwarding/disable     pause delivery without losing config
- POST   /audit-forwarding/enable      resume delivery
- DELETE /audit-forwarding             remove destination entirely
- POST   /audit-forwarding/test        deliver a synthetic event now
- GET    /audit-forwarding/deliveries  recent delivery log (this workspace)
- POST   /audit-forwarding/replay      re-enqueue one delivery by id

Every route is admin-only and tenant-scoped: a workspace can only see
and act on its own destination and delivery log. Cross tenant access
returns 404 by design so a probe cannot enumerate other workspaces.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .. import audit_forwarder
from ..auth import require_api_key, require_mfa, require_roles
from ..dry_run import is_dry_run, preview
from ..tenant import current_tenant

router = APIRouter(
    tags=["audit-forwarding"],
    prefix="/audit-forwarding",
    dependencies=[Depends(require_api_key)],
)


class DestinationOut(BaseModel):
    id: str
    url: str
    enabled: bool
    secret_hint: str
    created_at: float
    updated_at: float
    last_attempt_at: float
    last_success_at: float
    last_status: int
    last_error: str


class StatusResponse(BaseModel):
    configured: bool
    destination: DestinationOut | None = None


class UpsertBody(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class UpsertResponse(BaseModel):
    destination: DestinationOut
    secret: str = Field(
        description=(
            "Signing secret for this destination. Shown exactly once. "
            "Verify HMAC-SHA256 on the raw request body and compare to "
            "the X-ClawHum-Signature header value (prefixed sha256=)."
        )
    )


def _to_out(d: audit_forwarder.Destination) -> DestinationOut:
    return DestinationOut(
        id=d.id,
        url=d.url,
        enabled=d.enabled,
        secret_hint=d.secret_hint,
        created_at=d.created_at,
        updated_at=d.updated_at,
        last_attempt_at=d.last_attempt_at,
        last_success_at=d.last_success_at,
        last_status=d.last_status,
        last_error=d.last_error,
    )


@router.get(
    "",
    response_model=StatusResponse,
    dependencies=[Depends(require_roles("admin"))],
)
async def get_status(tenant_id: str = Depends(current_tenant)) -> StatusResponse:
    dest = audit_forwarder.get_destination(tenant_id)
    return StatusResponse(
        configured=dest is not None,
        destination=_to_out(dest) if dest is not None else None,
    )


@router.put(
    "",
    response_model=UpsertResponse,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def upsert(
    body: UpsertBody,
    request: Request,
    tenant_id: str = Depends(current_tenant),
) -> UpsertResponse:
    if is_dry_run(request):
        return JSONResponse(
            preview(
                "audit_forwarder_destination",
                tenant_id,
                url=body.url,
            )
        )
    try:
        dest, secret = audit_forwarder.upsert_destination(tenant_id, body.url)
    except audit_forwarder.DestinationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Cache the plaintext in process so the async worker can sign.
    audit_forwarder.get_worker().register_secret(tenant_id, secret)
    audit_forwarder.get_worker().start()
    return UpsertResponse(destination=_to_out(dest), secret=secret)


@router.post(
    "/disable",
    response_model=DestinationOut,
    dependencies=[Depends(require_roles("admin"))],
)
async def disable(tenant_id: str = Depends(current_tenant)) -> DestinationOut:
    updated = audit_forwarder.set_enabled(tenant_id, False)
    if updated is None:
        raise HTTPException(status_code=404, detail="no destination configured")
    return _to_out(updated)


@router.post(
    "/enable",
    response_model=DestinationOut,
    dependencies=[Depends(require_roles("admin"))],
)
async def enable(tenant_id: str = Depends(current_tenant)) -> DestinationOut:
    updated = audit_forwarder.set_enabled(tenant_id, True)
    if updated is None:
        raise HTTPException(status_code=404, detail="no destination configured")
    return _to_out(updated)


@router.delete(
    "",
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def delete(
    request: Request, tenant_id: str = Depends(current_tenant)
) -> Response:
    if is_dry_run(request):
        return JSONResponse(preview("audit_forwarder_destination_delete", tenant_id))
    removed = audit_forwarder.delete_destination(tenant_id)
    if not removed:
        raise HTTPException(status_code=404, detail="no destination configured")
    return Response(status_code=204)


class TestResponse(BaseModel):
    http_status: int
    duration_ms: float
    error: str


class TestBody(BaseModel):
    secret: str = Field(
        min_length=8,
        description="The plaintext secret returned from PUT /audit-forwarding.",
    )


@router.post(
    "/test",
    response_model=TestResponse,
    dependencies=[Depends(require_roles("admin"))],
)
async def test(
    body: TestBody,
    request: Request,
    tenant_id: str = Depends(current_tenant),
) -> TestResponse:
    dest = audit_forwarder.get_destination(tenant_id)
    if dest is None:
        raise HTTPException(status_code=404, detail="no destination configured")
    sample = {
        "ts": time.time(),
        "actor": "audit-forwarder/test",
        "tenant_id": tenant_id,
        "method": "POST",
        "path": "/audit-forwarding/test",
        "status": 200,
        "request_id": getattr(request.state, "request_id", None),
        "user_agent": request.headers.get("user-agent"),
        "client_ip": request.client.host if request.client else None,
        "duration_ms": 0.0,
        "test": True,
    }
    code, err, dur = audit_forwarder.deliver_with_test_secret(dest, body.secret, sample)
    return TestResponse(http_status=code, duration_ms=round(dur, 2), error=err)


class DeliveryRow(BaseModel):
    delivery_id: str
    destination_id: str
    event_ts: float
    attempt: int
    status: str
    http_status: int
    error: str
    duration_ms: float
    request_id: str | None = None
    event_path: str
    event_method: str
    event_actor: str


class DeliveriesResponse(BaseModel):
    items: list[DeliveryRow]


@router.get(
    "/deliveries",
    response_model=DeliveriesResponse,
    dependencies=[Depends(require_roles("admin"))],
)
async def deliveries(
    limit: int = 50,
    tenant_id: str = Depends(current_tenant),
) -> DeliveriesResponse:
    limit = max(1, min(200, int(limit)))
    rows = audit_forwarder.list_deliveries(tenant_id, limit=limit)
    items = [DeliveryRow(**{k: r.get(k) for k in DeliveryRow.model_fields}) for r in rows]
    return DeliveriesResponse(items=items)


class ReplayBody(BaseModel):
    delivery_id: str


@router.post(
    "/replay",
    response_model=TestResponse,
    dependencies=[Depends(require_roles("admin"))],
)
async def replay(
    body: ReplayBody, tenant_id: str = Depends(current_tenant)
) -> TestResponse:
    rows = audit_forwarder.list_deliveries(tenant_id, limit=500)
    target = next((r for r in rows if r.get("delivery_id") == body.delivery_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="delivery not found")
    # Reconstruct a minimal event from the recorded fields. Re-enqueue
    # for asynchronous redelivery; admins poll /deliveries to see the
    # outcome.
    event: dict[str, Any] = {
        "ts": target.get("event_ts") or time.time(),
        "tenant_id": tenant_id,
        "actor": target.get("event_actor") or "",
        "method": target.get("event_method") or "POST",
        "path": target.get("event_path") or "",
        "request_id": target.get("request_id"),
        "replay_of": body.delivery_id,
    }
    audit_forwarder.replay_event(tenant_id, event)
    return TestResponse(http_status=0, duration_ms=0.0, error="enqueued for async redelivery")
