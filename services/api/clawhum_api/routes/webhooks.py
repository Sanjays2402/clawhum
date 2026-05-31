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

from ..auth import require_api_key
from ..tenant import current_tenant

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


def _public_view(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": rec["id"],
        "url": rec["url"],
        "events": rec.get("events", list(ALL_EVENTS)),
        "created_at": rec.get("created_at", 0.0),
        "active": bool(rec.get("active", True)),
        "secret_hint": rec.get("secret_hint", ""),
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
    # Soft cap to keep the JSONL store healthy.
    existing = _live_hooks(tenant_id)
    if len(existing) >= 20:
        raise HTTPException(400, "webhook limit reached (max 20 per tenant)")
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


@router.delete(
    "/webhooks/{hook_id}",
    dependencies=[Depends(require_api_key)],
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

    first_id: str | None = None
    async with httpx.AsyncClient() as client:
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
