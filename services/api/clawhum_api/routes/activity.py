"""Unified activity feed for a tenant.

Returns the most recent things that happened on a user's account in
one timeline so the UI can render an "inbox" / "what's new since I
was last here" view without making the client stitch together
history, webhook deliveries, and shares by hand.

Items currently emitted:
- ``match``    one collapsed history row (a saved hum/run)
- ``delivery`` one webhook delivery attempt (success or failure)

Tenant scoping is enforced by reusing the same helpers the source
routes use, so the feed can never leak across tenants. Storage stays
in the same JSONL files; no new files, no migrations.

Filtering:
- ``since`` (unix seconds, float) returns only items strictly newer.
- ``kind``  optional, restricts to one item kind.
- ``limit`` capped at 200.

Response also carries ``latest_at`` (the newest item's timestamp, or
0.0 when empty) so the client can store a cursor and compute an
unread badge on the next page load.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from clawhum_api.auth import require_api_key
from clawhum_api.tenant import current_tenant
from clawhum_api.routes import history as history_routes
from clawhum_api.routes import webhooks as webhook_routes

router = APIRouter(tags=["activity"])

ItemKind = Literal["match", "delivery"]


class ActivityItem(BaseModel):
    id: str
    kind: ItemKind
    title: str
    subtitle: str
    ok: bool
    created_at: float
    href: str


class ActivityResponse(BaseModel):
    items: list[ActivityItem]
    total: int
    latest_at: float


def _match_items(tenant_id: str) -> list[ActivityItem]:
    out: list[ActivityItem] = []
    for rec in history_routes._collapse(tenant_id).values():
        results = rec.get("results") or []
        top = results[0] if results else {}
        title = (
            rec.get("name")
            or (str(top.get("title")) if top else "")
            or rec.get("filename")
            or "match"
        )
        artist = str(top.get("artist") or "") if top else ""
        count = int(rec.get("count") or len(results))
        subtitle = (
            f"top match {top.get('title')} by {artist}"
            if top and artist
            else f"{count} candidate{'s' if count != 1 else ''}"
        )
        out.append(
            ActivityItem(
                id=f"match:{rec['id']}",
                kind="match",
                title=str(title)[:120],
                subtitle=subtitle[:160],
                ok=True,
                created_at=float(rec.get("created_at") or 0.0),
                href=f"/matches/{rec['id']}",
            )
        )
    return out


def _delivery_items(tenant_id: str) -> list[ActivityItem]:
    out: list[ActivityItem] = []
    # Url lookup so the subtitle can show which endpoint received the call.
    urls: dict[str, str] = {}
    for rec in webhook_routes._iter_jsonl(webhook_routes._hooks_path()):
        if rec.get("tenant_id") != tenant_id:
            continue
        hid = rec.get("id")
        if isinstance(hid, str):
            urls[hid] = str(rec.get("url") or "")
    for rec in webhook_routes._iter_jsonl(webhook_routes._deliveries_path()):
        if rec.get("tenant_id") != tenant_id:
            continue
        hid = str(rec.get("webhook_id") or "")
        ok = bool(rec.get("ok"))
        status = int(rec.get("status") or 0)
        attempt = int(rec.get("attempt") or 1)
        event = str(rec.get("event") or "event")
        url = urls.get(hid, "")
        host = ""
        if url:
            # Cheap host extract; avoids importing urllib for one line.
            tail = url.split("://", 1)[-1]
            host = tail.split("/", 1)[0]
        subtitle = (
            f"{event} -> {host or hid} (HTTP {status}, attempt {attempt})"
            if status
            else f"{event} -> {host or hid} (no response, attempt {attempt})"
        )
        out.append(
            ActivityItem(
                id=f"delivery:{rec.get('id')}",
                kind="delivery",
                title=("delivered" if ok else "delivery failed"),
                subtitle=subtitle[:200],
                ok=ok,
                created_at=float(rec.get("created_at") or 0.0),
                href="/webhooks",
            )
        )
    return out


@router.get(
    "/activity",
    response_model=ActivityResponse,
    dependencies=[Depends(require_api_key)],
)
async def list_activity(
    request: Request,
    since: float = Query(default=0.0, ge=0.0),
    kind: str = Query(default="", pattern="^(|match|delivery)$"),
    limit: int = Query(default=50, ge=1, le=200),
    tenant_id: str = Depends(current_tenant),
) -> ActivityResponse:
    items: list[ActivityItem] = []
    if kind in ("", "match"):
        items.extend(_match_items(tenant_id))
    if kind in ("", "delivery"):
        items.extend(_delivery_items(tenant_id))
    if since > 0:
        items = [i for i in items if i.created_at > since]
    items.sort(key=lambda i: i.created_at, reverse=True)
    total = len(items)
    page = items[:limit]
    latest_at = page[0].created_at if page else 0.0
    return ActivityResponse(items=page, total=total, latest_at=latest_at)
