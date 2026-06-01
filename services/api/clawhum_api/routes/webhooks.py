"""User-registered outbound webhooks.

Each tenant can register one or more URLs that receive a POST when a
match completes. Deliveries are signed with HMAC-SHA256 over the raw
body using the webhook's per-endpoint secret (shown once at create),
sent in the ``X-Clawhum-Signature`` header as ``sha256=<hex>``.
Failed deliveries are retried with exponential backoff up to
``webhook_max_attempts`` and every attempt is appended to a delivery
log that the owner can read back.

Storage follows the existing JSONL pattern used by feedback/share so
no new infra is needed. The webhook list is tenant scoped on read and
on delete; the delivery log is the same. Secrets are stored hashed
(sha256) so a leaked file does not give the attacker the signing key,
matching how API keys are handled in api_keys.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

import httpx
from clawhum_core.logging import get_logger
from clawhum_core.settings import get_settings
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, HttpUrl

from ..auth import require_api_key, require_mfa, require_roles
from ..tenant import current_tenant
from .. import webhook_safety
from .. import webhook_delivery_rate
from ..audit import write_event as audit_write_event

router = APIRouter(tags=["webhooks"])
log = get_logger("clawhum.webhooks")

_HOOK_LOCK = Lock()
_DELIVERY_LOCK = Lock()
_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_ID_LEN = 12

# Events we support today. Easy to extend; clients filter at create time.
EVENT_MATCH_COMPLETED = "match.completed"
EVENT_WEBHOOK_TEST = "webhook.test"
# Events the owner can subscribe to at create time. ``webhook.test`` is
# always deliverable via the test endpoint regardless of subscription so
# users can verify reachability before any real event fires.
ALL_EVENTS = (EVENT_MATCH_COMPLETED,)


def _new_id() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_ID_LEN))


def _new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(24)


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _hooks_path() -> Path:
    p = get_settings().webhooks_path
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _deliveries_path() -> Path:
    p = get_settings().webhook_deliveries_path
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _live_hooks(tenant_id: str) -> list[dict[str, Any]]:
    """Reduce the JSONL log into the current set of webhooks for a tenant.

    The log is append-only; a record with ``deleted=True`` tombstones an id.
    We walk forward so the latest record for an id wins.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for rec in _iter_jsonl(_hooks_path()):
        if rec.get("tenant_id") != tenant_id:
            continue
        by_id[rec["id"]] = rec
    return [r for r in by_id.values() if not r.get("deleted")]


def _find_hook_any_tenant(hook_id: str) -> dict[str, Any] | None:
    by_id: dict[str, dict[str, Any]] = {}
    for rec in _iter_jsonl(_hooks_path()):
        by_id[rec["id"]] = rec
    rec = by_id.get(hook_id)
    if rec is None or rec.get("deleted"):
        return None
    return rec


def _append_hook(rec: dict[str, Any]) -> None:
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
    with _HOOK_LOCK:
        with _hooks_path().open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _append_delivery(rec: dict[str, Any]) -> None:
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
    with _DELIVERY_LOCK:
        with _deliveries_path().open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _consecutive_failures_for(hook_id: str, tenant_id: str, since_ts: float) -> int:
    """Count consecutive failed deliveries for ``hook_id`` from most recent
    backward, stopping at the first success or at ``since_ts`` (typically the
    last ``resumed_at`` so a fresh resume starts the budget over).
    """
    records = [
        r for r in _iter_jsonl(_deliveries_path())
        if r.get("webhook_id") == hook_id and r.get("tenant_id") == tenant_id
    ]
    records.sort(key=lambda r: float(r.get("created_at") or 0.0))
    streak = 0
    for r in reversed(records):
        if float(r.get("created_at") or 0.0) < since_ts:
            break
        if r.get("ok"):
            break
        streak += 1
    return streak


def _maybe_auto_disable(hook: dict[str, Any]) -> dict[str, Any] | None:
    """After a failed delivery, evaluate the circuit breaker.

    If the consecutive failure streak (since the most recent resume or
    create) has reached ``webhook_auto_disable_threshold`` and the hook
    is still active, append a new hook record flipping ``active`` to
    False with ``auto_disabled_at`` and ``auto_disabled_reason`` set.
    """
    s = get_settings()
    threshold = int(getattr(s, "webhook_auto_disable_threshold", 0) or 0)
    if threshold <= 0:
        return None
    tenant_id = hook.get("tenant_id", "")
    hook_id = hook["id"]
    current = _find_hook_any_tenant(hook_id)
    if current is None or not current.get("active", True):
        return None
    boundary = float(current.get("resumed_at") or current.get("created_at") or 0.0)
    streak = _consecutive_failures_for(hook_id, tenant_id, boundary)
    if streak < threshold:
        return None
    rec = dict(current)
    rec["active"] = False
    rec["auto_disabled_at"] = time.time()
    rec["auto_disabled_reason"] = (
        f"{streak} consecutive delivery failures (threshold {threshold})"
    )
    rec["consecutive_failures"] = streak
    _append_hook(rec)
    log.warning(
        "webhook_auto_disabled",
        webhook_id=hook_id,
        tenant_id=tenant_id,
        consecutive_failures=streak,
        threshold=threshold,
    )
    try:
        from ..audit import write_event as _audit_write
        _audit_write({
            "ts": time.time(),
            "actor": "system:webhook-circuit-breaker",
            "tenant_id": tenant_id,
            "method": "SYSTEM",
            "path": f"/webhooks/{hook_id}/auto-disable",
            "status": 200,
            "action": "webhook.auto_disabled",
            "target": hook_id,
            "detail": {
                "consecutive_failures": streak,
                "threshold": threshold,
                "url": current.get("url"),
            },
        })
    except Exception:  # pragma: no cover - audit best effort
        log.warning("webhook_auto_disable_audit_failed", webhook_id=hook_id)
    return rec


def _public_view(rec: dict[str, Any]) -> dict[str, Any]:
    prev_hint = rec.get("previous_secret_hint") or None
    prev_exp = rec.get("previous_secret_expires_at") or 0.0
    # Hide an expired previous secret from the public view so the UI
    # cannot show a misleading rotation badge after the grace window.
    if prev_hint and prev_exp and prev_exp <= time.time():
        prev_hint = None
        prev_exp = 0.0
    # Live consecutive failure count since the most recent resume so
    # operators can see a hook trending toward auto disable before it
    # actually trips the breaker.
    boundary = float(rec.get("resumed_at") or rec.get("created_at") or 0.0)
    try:
        streak = _consecutive_failures_for(rec["id"], rec.get("tenant_id", ""), boundary)
    except Exception:  # pragma: no cover - never fail the list call
        streak = int(rec.get("consecutive_failures") or 0)
    return {
        "id": rec["id"],
        "url": rec["url"],
        "events": rec.get("events", list(ALL_EVENTS)),
        "created_at": rec.get("created_at", 0.0),
        "active": bool(rec.get("active", True)),
        "secret_hint": rec.get("secret_hint", ""),
        "previous_secret_hint": prev_hint,
        "previous_secret_expires_at": prev_exp or None,
        "rotated_at": rec.get("rotated_at") or None,
        "paused_at": rec.get("paused_at") or None,
        "resumed_at": rec.get("resumed_at") or None,
        "auto_disabled_at": rec.get("auto_disabled_at") or None,
        "auto_disabled_reason": rec.get("auto_disabled_reason") or None,
        "consecutive_failures": int(streak),
    }


class WebhookCreateBody(BaseModel):
    url: HttpUrl
    events: list[str] = Field(default_factory=lambda: list(ALL_EVENTS))


class WebhookCreateResponse(BaseModel):
    id: str
    url: str
    events: list[str]
    secret: str  # full secret, returned ONCE
    created_at: float


class WebhookListItem(BaseModel):
    id: str
    url: str
    events: list[str]
    created_at: float
    active: bool
    secret_hint: str
    previous_secret_hint: str | None = None
    previous_secret_expires_at: float | None = None
    rotated_at: float | None = None
    paused_at: float | None = None
    resumed_at: float | None = None
    auto_disabled_at: float | None = None
    auto_disabled_reason: str | None = None
    consecutive_failures: int = 0


class RotateSecretBody(BaseModel):
    # 0 means invalidate the old secret immediately (no overlap window).
    # Cap matches webhook_max_attempts retry envelope: a week is plenty
    # for any reasonable receiver-side rotation deployment.
    grace_seconds: int = Field(default=86400, ge=0, le=604800)


class RotateSecretResponse(BaseModel):
    id: str
    secret: str  # full new secret, returned ONCE
    previous_secret_expires_at: float | None
    rotated_at: float


class WebhookListResponse(BaseModel):
    webhooks: list[WebhookListItem]


class DeliveryItem(BaseModel):
    id: str
    webhook_id: str
    event: str
    attempt: int
    status: int  # http status, or 0 on transport failure
    ok: bool
    elapsed_ms: int
    error: str | None = None
    created_at: float
    redelivery_of: str | None = None
    replayable: bool = False


class DeliveryListResponse(BaseModel):
    deliveries: list[DeliveryItem]


@router.post(
    "/webhooks",
    response_model=WebhookCreateResponse,
    dependencies=[Depends(require_api_key)],
)
async def create_webhook(
    body: WebhookCreateBody,
    tenant_id: str = Depends(current_tenant),
) -> WebhookCreateResponse:
    events = [e for e in body.events if e in ALL_EVENTS]
    if not events:
        raise HTTPException(400, f"events must include one of {list(ALL_EVENTS)}")
    # Per-workspace cap on registered destinations. Admins manage the
    # ceiling at PUT /webhook-destination-cap; the legacy soft cap of
    # 20 is preserved as the default for tenants with no policy row.
    from .. import webhook_destination_cap as _whcap
    existing = _live_hooks(tenant_id)
    try:
        _whcap.assert_capacity(tenant_id, live_count=len(existing))
    except _whcap.WebhookDestinationCapExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "webhook_destination_cap_exceeded",
                "message": str(exc),
                "live": exc.live,
                "max_active": exc.max_active,
            },
            headers={"Retry-After": "0"},
        )
    # SSRF policy: refuse to register destinations that point at internal
    # ranges or cloud metadata endpoints. Re-checked at delivery time too.
    try:
        webhook_safety.validate_destination(str(body.url), tenant_id)
    except webhook_safety.WebhookDestinationError as e:
        # Surface the workspace HTTPS policy block as a structured error
        # so dashboards and integrators can branch on a stable code.
        from .. import webhook_policy as _wp
        if _wp.require_https(tenant_id) and str(body.url).lower().startswith("http://"):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "webhook_https_required",
                    "message": "workspace policy requires https for webhook destinations",
                },
            )
        raise HTTPException(400, str(e))
    hook_id = _new_id()
    secret = _new_secret()
    now = time.time()
    rec = {
        "id": hook_id,
        "tenant_id": tenant_id,
        "url": str(body.url),
        "events": events,
        "created_at": now,
        "active": True,
        "secret_hash": _hash_secret(secret),
        "secret_hint": f"{secret[:10]}...{secret[-4:]}",
    }
    _append_hook(rec)
    return WebhookCreateResponse(
        id=hook_id,
        url=str(body.url),
        events=events,
        secret=secret,
        created_at=now,
    )


@router.get(
    "/webhooks",
    response_model=WebhookListResponse,
    dependencies=[Depends(require_api_key)],
)
async def list_webhooks(
    tenant_id: str = Depends(current_tenant),
) -> WebhookListResponse:
    items = [WebhookListItem(**_public_view(r)) for r in _live_hooks(tenant_id)]
    items.sort(key=lambda i: i.created_at, reverse=True)
    return WebhookListResponse(webhooks=items)


class EgressIpsResponse(BaseModel):
    """Source addresses this deployment dispatches webhooks from.

    Returned by ``GET /v1/webhooks/egress-ips`` so an enterprise
    customer's network team can pin a firewall allowlist instead of
    asking support for the list. ``pinned`` is false when the operator
    has not configured ``CLAWHUM_WEBHOOK_EGRESS_IPS``; in that case
    SecOps should treat the egress as dynamic (e.g. behind a NAT or
    serverless platform) and either ask the operator to pin it or
    allow the receiver to live behind a public address.
    """

    pinned: bool
    addresses: list[str]
    updated_at: str
    note: str


def _parse_egress_list(raw: str) -> list[str]:
    """Validate and normalise CLAWHUM_WEBHOOK_EGRESS_IPS.

    Accepts comma or whitespace separated IPv4/IPv6 addresses and CIDRs.
    Invalid entries are dropped silently rather than 500ing the endpoint,
    so a typo in ops config does not take the disclosure offline; the
    valid subset is still useful and the operator sees the discrepancy
    against what they configured.
    """
    import ipaddress
    out: list[str] = []
    seen: set[str] = set()
    for chunk in (raw or "").replace(",", " ").split():
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            if "/" in chunk:
                net = ipaddress.ip_network(chunk, strict=False)
                norm = str(net)
            else:
                addr = ipaddress.ip_address(chunk)
                norm = str(addr)
        except ValueError:
            continue
        if norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


@router.get(
    "/webhooks/egress-ips",
    response_model=EgressIpsResponse,
    dependencies=[Depends(require_api_key)],
)
async def webhook_egress_ips() -> EgressIpsResponse:
    """Disclose the outbound source IPs this deployment uses for webhooks.

    Auth required (any valid API key or PAT) so the list is not handed
    to anonymous scanners, but no role gate: every member of every
    tenant needs to be able to share it with their own network team.
    """
    settings = get_settings()
    addrs = _parse_egress_list(settings.webhook_egress_ips)
    pinned = bool(addrs)
    note = (
        "Add these source addresses to your firewall allowlist for the"
        " destination URLs you register with Clawhum webhooks."
        if pinned
        else (
            "This deployment has not pinned its egress addresses. Ask the"
            " operator to set CLAWHUM_WEBHOOK_EGRESS_IPS, or accept webhook"
            " delivery from a dynamic source range."
        )
    )
    return EgressIpsResponse(
        pinned=pinned,
        addresses=addrs,
        updated_at=settings.webhook_egress_updated_at or "",
        note=note,
    )


@router.delete(
    "/webhooks/{hook_id}",
    dependencies=[Depends(require_api_key), Depends(require_mfa())],
)
async def delete_webhook(
    hook_id: str,
    request: Request,
    tenant_id: str = Depends(current_tenant),
) -> dict[str, Any]:
    if not hook_id.isalnum() or len(hook_id) > 32:
        raise HTTPException(404, "not found")
    current = {r["id"]: r for r in _live_hooks(tenant_id)}
    if hook_id not in current:
        raise HTTPException(404, "not found")
    from ..dry_run import is_dry_run, preview
    if is_dry_run(request):
        return preview("webhook", hook_id, tenant_id=tenant_id,
                       url=current[hook_id]["url"],
                       events=current[hook_id].get("events", []))
    tomb = {
        "id": hook_id,
        "tenant_id": tenant_id,
        "url": current[hook_id]["url"],
        "events": current[hook_id].get("events", []),
        "created_at": current[hook_id].get("created_at", 0.0),
        "deleted": True,
        "deleted_at": time.time(),
    }
    _append_hook(tomb)
    return {"ok": True, "id": hook_id}


class PauseResponse(BaseModel):
    id: str
    active: bool
    paused_at: float | None = None
    resumed_at: float | None = None


def _set_active(hook_id: str, tenant_id: str, request: Request, active: bool) -> dict[str, Any]:
    """Toggle a webhook's active flag via a new append-only record.

    Inactive webhooks are skipped by the dispatcher (see ``deliver_event``)
    while preserving id, URL, events, secret hash, and delivery history,
    so an admin can suspend an endpoint during an incident without losing
    its config or rotating its receiver-side secret.
    """
    if not hook_id.isalnum() or len(hook_id) > 32:
        raise HTTPException(404, "not found")
    current = {r["id"]: r for r in _live_hooks(tenant_id)}
    hook = current.get(hook_id)
    if hook is None:
        raise HTTPException(404, "not found")
    if bool(hook.get("active", True)) == active:
        # Idempotent: report current state without writing a new record.
        return {
            "id": hook_id,
            "active": active,
            "paused_at": hook.get("paused_at"),
            "resumed_at": hook.get("resumed_at"),
        }
    from ..dry_run import is_dry_run, preview
    if is_dry_run(request):
        return preview(
            "webhook_pause" if not active else "webhook_resume",
            hook_id,
            tenant_id=tenant_id,
            url=hook["url"],
        )
    now = time.time()
    rec = dict(hook)
    rec["active"] = active
    if active:
        rec["resumed_at"] = now
        # Clear any prior auto disable so the breaker resets and the UI
        # stops showing a stale red badge after a manual resume.
        rec.pop("auto_disabled_at", None)
        rec.pop("auto_disabled_reason", None)
        rec["consecutive_failures"] = 0
    else:
        rec["paused_at"] = now
    _append_hook(rec)
    return {
        "id": hook_id,
        "active": active,
        "paused_at": rec.get("paused_at"),
        "resumed_at": rec.get("resumed_at"),
    }


@router.post(
    "/webhooks/{hook_id}/pause",
    response_model=PauseResponse,
    dependencies=[
        Depends(require_api_key),
        Depends(require_roles("admin")),
        Depends(require_mfa()),
    ],
)
async def pause_webhook(
    hook_id: str,
    request: Request,
    tenant_id: str = Depends(current_tenant),
) -> dict[str, Any]:
    """Suspend deliveries to a webhook without deleting it.

    Inactive hooks stay listed and keep their delivery history; the
    dispatcher (``deliver_event``) filters them out so no outbound
    request is made until ``/resume`` is called.
    """
    return _set_active(hook_id, tenant_id, request, active=False)


@router.post(
    "/webhooks/{hook_id}/resume",
    response_model=PauseResponse,
    dependencies=[
        Depends(require_api_key),
        Depends(require_roles("admin")),
        Depends(require_mfa()),
    ],
)
async def resume_webhook(
    hook_id: str,
    request: Request,
    tenant_id: str = Depends(current_tenant),
) -> dict[str, Any]:
    """Re-enable deliveries to a previously paused webhook."""
    return _set_active(hook_id, tenant_id, request, active=True)


@router.post(
    "/webhooks/{hook_id}/rotate-secret",
    dependencies=[
        Depends(require_api_key),
        Depends(require_roles("admin")),
        Depends(require_mfa()),
    ],
)
async def rotate_webhook_secret(
    hook_id: str,
    body: RotateSecretBody,
    request: Request,
    tenant_id: str = Depends(current_tenant),
) -> dict[str, Any]:
    """Rotate a webhook's signing secret with an optional overlap window.

    Receivers cannot atomically swap signing keys; for any grace_seconds
    > 0 outbound deliveries during the window carry both the new
    signature (``X-Clawhum-Signature``) and the previous one
    (``X-Clawhum-Signature-Previous``) so the receiver can accept either
    while it deploys the new key. Setting grace_seconds=0 invalidates
    the old secret immediately, which is the right choice for incident
    response.

    The new plaintext is returned exactly once, matching create.
    """
    if not hook_id.isalnum() or len(hook_id) > 32:
        raise HTTPException(404, "not found")
    current = {r["id"]: r for r in _live_hooks(tenant_id)}
    hook = current.get(hook_id)
    if hook is None:
        raise HTTPException(404, "not found")

    from ..dry_run import is_dry_run, preview
    if is_dry_run(request):
        return preview(
            "webhook_secret_rotation",
            hook_id,
            tenant_id=tenant_id,
            url=hook["url"],
            grace_seconds=body.grace_seconds,
        )

    now = time.time()
    new_secret = _new_secret()
    new_hint = f"{new_secret[:10]}...{new_secret[-4:]}"
    prev_hash = hook.get("secret_hash")
    prev_hint = hook.get("secret_hint")
    prev_expires_at: float | None
    if body.grace_seconds > 0 and prev_hash:
        prev_expires_at = now + body.grace_seconds
    else:
        prev_expires_at = None
        prev_hash = None
        prev_hint = None

    rec = {
        "id": hook_id,
        "tenant_id": tenant_id,
        "url": hook["url"],
        "events": hook.get("events", list(ALL_EVENTS)),
        "created_at": hook.get("created_at", now),
        "active": bool(hook.get("active", True)),
        "secret_hash": _hash_secret(new_secret),
        "secret_hint": new_hint,
        "previous_secret_hash": prev_hash,
        "previous_secret_hint": prev_hint,
        "previous_secret_expires_at": prev_expires_at,
        "rotated_at": now,
    }
    _append_hook(rec)

    # Promote any test-only plaintext override so signature assertions in
    # tests still work after a rotation. Receivers in production track
    # their own secret; this seam only matters when the API also signs.
    old_plain = _PLAINTEXT_OVERRIDES.get(hook_id)
    if old_plain and prev_expires_at:
        _PLAINTEXT_PREVIOUS[hook_id] = (old_plain, prev_expires_at)
    else:
        _PLAINTEXT_PREVIOUS.pop(hook_id, None)
    return RotateSecretResponse(
        id=hook_id,
        secret=new_secret,
        previous_secret_expires_at=prev_expires_at,
        rotated_at=now,
    ).model_dump()


@router.get(
    "/webhooks/{hook_id}/deliveries",
    response_model=DeliveryListResponse,
    dependencies=[Depends(require_api_key)],
)
async def list_deliveries(
    hook_id: str,
    tenant_id: str = Depends(current_tenant),
    limit: int = 100,
) -> DeliveryListResponse:
    if not hook_id.isalnum() or len(hook_id) > 32:
        raise HTTPException(404, "not found")
    owned = {r["id"] for r in _live_hooks(tenant_id)}
    if hook_id not in owned:
        # Also allow viewing deliveries for a tombstoned hook the tenant owned.
        archived = [
            r for r in _iter_jsonl(_hooks_path())
            if r.get("id") == hook_id and r.get("tenant_id") == tenant_id
        ]
        if not archived:
            raise HTTPException(404, "not found")
    out: list[DeliveryItem] = []
    for rec in _iter_jsonl(_deliveries_path()):
        if rec.get("webhook_id") != hook_id:
            continue
        if rec.get("tenant_id") != tenant_id:
            continue
        out.append(DeliveryItem(
            id=rec["id"],
            webhook_id=rec["webhook_id"],
            event=rec.get("event", ""),
            attempt=int(rec.get("attempt", 1)),
            status=int(rec.get("status", 0)),
            ok=bool(rec.get("ok", False)),
            elapsed_ms=int(rec.get("elapsed_ms", 0)),
            error=rec.get("error"),
            created_at=float(rec.get("created_at", 0.0)),
            redelivery_of=rec.get("redelivery_of"),
            replayable=bool(rec.get("payload") is not None),
        ))
    out.sort(key=lambda i: i.created_at, reverse=True)
    return DeliveryListResponse(deliveries=out[:max(1, min(limit, 500))])


class TriggerResponse(BaseModel):
    ok: bool
    delivery_id: str
    event: str


@router.post(
    "/webhooks/{hook_id}/test",
    response_model=TriggerResponse,
    dependencies=[Depends(require_api_key)],
)
async def test_webhook(
    hook_id: str,
    tenant_id: str = Depends(current_tenant),
) -> TriggerResponse:
    """Fire a synthetic ``webhook.test`` event to the registered URL.

    Returns once the first delivery attempt has been recorded so the
    caller can immediately refresh the delivery log and see the result.
    Subsequent retries (on transport failure) happen in the same call.
    """
    if not hook_id.isalnum() or len(hook_id) > 32:
        raise HTTPException(404, "not found")
    owned = {r["id"]: r for r in _live_hooks(tenant_id)}
    hook = owned.get(hook_id)
    if hook is None:
        raise HTTPException(404, "not found")
    payload = {
        "event": EVENT_WEBHOOK_TEST,
        "webhook_id": hook_id,
        "sent_at": time.time(),
        "message": "clawhum webhook test ping",
    }
    delivery_id = await _deliver_one(
        hook,
        EVENT_WEBHOOK_TEST,
        payload,
        plain_secret=_PLAINTEXT_OVERRIDES.get(hook_id),
        store_payload=False,
    )
    return TriggerResponse(ok=True, delivery_id=delivery_id, event=EVENT_WEBHOOK_TEST)


@router.post(
    "/webhooks/{hook_id}/deliveries/{delivery_id}/redeliver",
    response_model=TriggerResponse,
    dependencies=[Depends(require_api_key)],
)
async def redeliver(
    hook_id: str,
    delivery_id: str,
    tenant_id: str = Depends(current_tenant),
) -> TriggerResponse:
    """Replay a past delivery's payload to the webhook URL.

    Only deliveries whose original payload was persisted are replayable;
    earlier records and test pings will return 422 so the UI can disable
    the button instead of pretending.
    """
    if not hook_id.isalnum() or len(hook_id) > 32:
        raise HTTPException(404, "not found")
    if not delivery_id.isalnum() or len(delivery_id) > 32:
        raise HTTPException(404, "not found")
    owned = {r["id"]: r for r in _live_hooks(tenant_id)}
    hook = owned.get(hook_id)
    if hook is None:
        raise HTTPException(404, "not found")
    original: dict[str, Any] | None = None
    for rec in _iter_jsonl(_deliveries_path()):
        if rec.get("id") == delivery_id and rec.get("webhook_id") == hook_id \
           and rec.get("tenant_id") == tenant_id:
            original = rec
            break
    if original is None:
        raise HTTPException(404, "delivery not found")
    payload = original.get("payload")
    if payload is None:
        raise HTTPException(
            422,
            "this delivery has no stored payload and cannot be replayed",
        )
    event = original.get("event") or EVENT_MATCH_COMPLETED
    new_id = await _deliver_one(
        hook,
        event,
        payload,
        plain_secret=_PLAINTEXT_OVERRIDES.get(hook_id),
        redelivery_of=delivery_id,
    )
    return TriggerResponse(ok=True, delivery_id=new_id, event=event)


# -----------------------------------------------------------------------------
# Dispatch helpers (used by /match)
# -----------------------------------------------------------------------------


def sign_body(secret: str, body: bytes) -> str:
    """Return the X-Clawhum-Signature header value for a raw body."""
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def _client_verify(tenant_id: str):
    """Return an SSLContext (or True) for outbound webhook deliveries.

    Honors the per-workspace ``min_tls_version`` policy by pinning the
    SSLContext's ``minimum_version``. Falls back to httpx's default
    (system trust + TLS defaults) when no floor is configured.
    """
    from .. import webhook_policy as _wp
    floor = _wp.min_tls_version(tenant_id)
    ctx = _wp.build_ssl_context(floor) if floor else None
    return ctx if ctx is not None else True


async def _post_once(
    client: httpx.AsyncClient,
    url: str,
    body: bytes,
    headers: dict[str, str],
    timeout: float,
) -> tuple[int, str | None]:
    try:
        r = await client.post(url, content=body, headers=headers, timeout=timeout)
        return r.status_code, None if r.is_success else f"http {r.status_code}"
    except httpx.HTTPError as e:
        return 0, str(e)[:200]


async def _deliver_one(
    hook: dict[str, Any],
    event: str,
    payload: dict[str, Any],
    *,
    plain_secret: str | None = None,
    redelivery_of: str | None = None,
    store_payload: bool = True,
) -> str:
    """Best-effort outbound delivery with retry + persistent log.

    ``plain_secret`` lets tests inject the secret directly; in production we
    never store it in the clear and skip signing in that case (the receiver
    only gets the hashed prefix as ``X-Clawhum-Signature-Hint``).

    Returns the delivery id of the first (or only) attempt so callers like
    the test-fire endpoint can hand it back to the user. The payload is
    persisted on each attempt so the owner can later redeliver from the
    UI; this is bounded by the JSONL log size like every other store.
    """
    s = get_settings()
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    base_headers = {
        "Content-Type": "application/json",
        "User-Agent": "clawhum-webhooks/1",
        "X-Clawhum-Event": event,
        "X-Clawhum-Webhook-Id": hook["id"],
        "X-Clawhum-Delivery-Id": _new_id(),
    }
    if redelivery_of:
        base_headers["X-Clawhum-Redelivery-Of"] = redelivery_of
    if plain_secret:
        base_headers["X-Clawhum-Signature"] = sign_body(plain_secret, body)
    else:
        base_headers["X-Clawhum-Signature-Hint"] = hook.get("secret_hint", "")

    # Rotation grace: include a signature against the previous secret
    # so receivers can accept either while they roll out the new key.
    prev_exp = hook.get("previous_secret_expires_at") or 0.0
    if prev_exp and prev_exp > time.time():
        prev_hint = hook.get("previous_secret_hint") or ""
        if prev_hint:
            base_headers["X-Clawhum-Signature-Hint-Previous"] = prev_hint
        prev_plain_pair = _PLAINTEXT_PREVIOUS.get(hook["id"])
        if prev_plain_pair and prev_plain_pair[1] > time.time():
            base_headers["X-Clawhum-Signature-Previous"] = sign_body(
                prev_plain_pair[0], body
            )
        base_headers["X-Clawhum-Previous-Secret-Expires"] = str(int(prev_exp))

    first_id: str | None = None
    tenant_id = hook.get("tenant_id", "")
    # Re-check SSRF policy on every delivery so a DNS rebind or a newly
    # tightened tenant allowlist actually takes effect. A block here is
    # recorded as a delivery failure (status=0) without an HTTP request
    # ever leaving the process, and we do not retry: the URL is wrong by
    # policy, not by transient transport failure.
    try:
        webhook_safety.validate_destination(hook["url"], tenant_id)
    except webhook_safety.WebhookDestinationError as policy_err:
        delivery_id = _new_id()
        rec: dict[str, Any] = {
            "id": delivery_id,
            "tenant_id": tenant_id,
            "webhook_id": hook["id"],
            "event": event,
            "attempt": 1,
            "status": 0,
            "ok": False,
            "elapsed_ms": 0,
            "error": f"blocked by destination policy: {policy_err}",
            "created_at": time.time(),
            "policy_blocked": True,
        }
        if redelivery_of:
            rec["redelivery_of"] = redelivery_of
        if store_payload:
            rec["payload"] = payload
        _append_delivery(rec)
        log.warning(
            "webhook_destination_blocked",
            webhook_id=hook["id"], tenant_id=tenant_id, reason=str(policy_err),
        )
        return delivery_id
    # Per-workspace per-hook delivery rate cap. Enforced sender side so
    # a runaway producer cannot fan out faster than the receiver has
    # told us they can absorb. A cap of 0 is the default (no cap).
    try:
        cap = webhook_delivery_rate.max_per_minute(tenant_id)
    except Exception:  # pragma: no cover - policy read must never break delivery
        cap = 0
    if cap > 0:
        boundary = time.time() - 60.0
        observed = 0
        for rec in _iter_jsonl(_deliveries_path()):
            if rec.get("webhook_id") != hook["id"]:
                continue
            if rec.get("tenant_id") != tenant_id:
                continue
            ts = float(rec.get("created_at") or 0.0)
            if ts < boundary:
                continue
            # Synthetic skip records do not count toward the budget;
            # only real attempts (HTTP issued or about to be) do.
            if rec.get("rate_limited") or rec.get("policy_blocked"):
                continue
            observed += 1
        if observed >= cap:
            delivery_id = _new_id()
            rec = {
                "id": delivery_id,
                "tenant_id": tenant_id,
                "webhook_id": hook["id"],
                "event": event,
                "attempt": 1,
                "status": 0,
                "ok": False,
                "elapsed_ms": 0,
                "error": (
                    f"rate_limited_by_policy: {cap}/min cap exceeded"
                    f" (observed {observed} in last 60s)"
                ),
                "created_at": time.time(),
                "rate_limited": True,
                "rate_cap": cap,
            }
            if redelivery_of:
                rec["redelivery_of"] = redelivery_of
            if store_payload:
                rec["payload"] = payload
            _append_delivery(rec)
            log.warning(
                "webhook_delivery_rate_limited",
                webhook_id=hook["id"], tenant_id=tenant_id,
                cap=cap, observed=observed,
            )
            try:
                audit_write_event(
                    {
                        "ts": rec["created_at"],
                        "actor": "system:webhook-delivery-rate",
                        "tenant_id": tenant_id,
                        "target": hook["id"],
                        "request_id": "",
                        "path": f"/webhooks/{hook['id']}/deliver",
                        "method": "INTERNAL",
                        "action": "webhook.rate_limited",
                        "status": 0,
                        "detail": {
                            "event": event,
                            "cap": cap,
                            "observed": observed,
                            "delivery_id": delivery_id,
                        },
                    }
                )
            except Exception:  # pragma: no cover - audit must never break delivery
                log.warning(
                    "webhook_rate_limit_audit_failed",
                    webhook_id=hook["id"],
                )
            return delivery_id
    async with httpx.AsyncClient(verify=_client_verify(tenant_id)) as client:
        for attempt in range(1, s.webhook_max_attempts + 1):
            t0 = time.perf_counter()
            status, err = await _post_once(
                client, hook["url"], body, base_headers, s.webhook_timeout_sec
            )
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            ok = 200 <= status < 300
            delivery_id = _new_id()
            if first_id is None:
                first_id = delivery_id
            rec: dict[str, Any] = {
                "id": delivery_id,
                "tenant_id": hook.get("tenant_id"),
                "webhook_id": hook["id"],
                "event": event,
                "attempt": attempt,
                "status": status,
                "ok": ok,
                "elapsed_ms": elapsed_ms,
                "error": err,
                "created_at": time.time(),
            }
            if redelivery_of:
                rec["redelivery_of"] = redelivery_of
            if store_payload:
                # Keep the payload small; we already cap match payloads at
                # the route level. Skip persistence for test events to keep
                # the log honest about real deliveries.
                rec["payload"] = payload
            _append_delivery(rec)
            if ok:
                log.info("webhook_delivered", webhook_id=hook["id"], attempt=attempt, status=status)
                return first_id
            log.warning(
                "webhook_delivery_failed",
                webhook_id=hook["id"], attempt=attempt, status=status, error=err,
            )
            if attempt < s.webhook_max_attempts:
                await asyncio.sleep(min(2 ** (attempt - 1), 8))
    # All attempts in this dispatch failed. Evaluate the circuit breaker
    # so a permanently broken receiver does not keep burning retry budget
    # for every subsequent event.
    try:
        _maybe_auto_disable(hook)
    except Exception:  # pragma: no cover - never let breaker raise
        log.warning("webhook_auto_disable_check_failed", webhook_id=hook["id"])
    return first_id or _new_id()


async def dispatch_event(tenant_id: str, event: str, payload: dict[str, Any]) -> int:
    """Fan out an event to all matching live webhooks for a tenant.

    Returns the number of dispatches scheduled. Each dispatch runs in the
    background so the caller is never blocked on the receiver.
    """
    if event not in ALL_EVENTS:
        return 0
    hooks = [h for h in _live_hooks(tenant_id) if event in h.get("events", []) and h.get("active", True)]
    if not hooks:
        return 0
    # We do not have the plain secret server-side any more; receivers
    # verify by recomputing the hash of the signing key they were shown
    # on create. In tests we pass _PLAINTEXT_OVERRIDES so signatures can
    # be asserted end to end.
    for h in hooks:
        plain = _PLAINTEXT_OVERRIDES.get(h["id"])  # test seam
        asyncio.create_task(_deliver_one(h, event, payload, plain_secret=plain))
    return len(hooks)


# Test-only seam: route handlers and integration tests can stash the plain
# secret here under the hook id so signatures can be exercised. Production
# code never populates this; the secret is shown once at create time and
# the receiver is expected to store it on their side.
_PLAINTEXT_OVERRIDES: dict[str, str] = {}

# Mirror for the previous (pre-rotation) plaintext during the grace
# window so tests can assert both signatures verify. Same production
# caveat: never populated outside tests.
_PLAINTEXT_PREVIOUS: dict[str, tuple[str, float]] = {}


# -----------------------------------------------------------------------------
# Destination allowlist (per workspace, admin only)
# -----------------------------------------------------------------------------


class AllowlistBody(BaseModel):
    hosts: list[str] = Field(default_factory=list, max_length=64)


class AllowlistResponse(BaseModel):
    tenant_id: str
    hosts: list[str]
    block_private_ips: bool
    note: str


def _allowlist_response(tenant_id: str) -> AllowlistResponse:
    return AllowlistResponse(
        tenant_id=tenant_id,
        hosts=webhook_safety.get_tenant_allowlist(tenant_id),
        block_private_ips=get_settings().webhook_block_private_ips,
        note=(
            "Outbound webhook destinations that resolve to internal,"
            " loopback, or cloud metadata addresses are blocked by"
            " default. Add a host suffix here to deliver to receivers on"
            " a private network you control. Cloud metadata endpoints"
            " stay denied regardless of this list."
        ),
    )


@router.get(
    "/webhooks/destination-allowlist",
    response_model=AllowlistResponse,
    dependencies=[Depends(require_api_key)],
)
async def get_destination_allowlist(
    tenant_id: str = Depends(current_tenant),
) -> AllowlistResponse:
    return _allowlist_response(tenant_id)


@router.put(
    "/webhooks/destination-allowlist",
    response_model=AllowlistResponse,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def put_destination_allowlist(
    body: AllowlistBody,
    request: Request,
    tenant_id: str = Depends(current_tenant),
) -> AllowlistResponse:
    from ..dry_run import is_dry_run, preview
    if is_dry_run(request):
        return preview(
            "webhook_destination_allowlist",
            tenant_id,
            tenant_id=tenant_id,
            hosts=body.hosts,
        )
    webhook_safety.set_tenant_allowlist(tenant_id, list(body.hosts))
    return _allowlist_response(tenant_id)
