"""Public shareable result URLs.

POST /share creates a record from a MatchResponse-shaped payload and
returns a short share_id. GET /share/{id} returns it without auth so
the `/r/<id>` page works in an incognito window.

Storage is a JSONL file at settings.shares_path so we keep the existing
dependency-free pattern used by feedback/audit. Records are tenant
scoped at write time, but reads are public by design (that is the point
of a share link). Share ids use 12 url-safe base32 chars from secrets
to make them hard to enumerate.
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from threading import Lock
from typing import Any

from clawhum_core.settings import get_settings
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import require_api_key
from ..schemas import MatchResult

router = APIRouter(tags=["share"])

_WRITE_LOCK = Lock()
_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"  # crockford-ish, no i/l/o/u
_ID_LEN = 12


def _new_id() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_ID_LEN))


def _store_path() -> Path:
    p = get_settings().shares_path
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _iter_records():
    p = _store_path()
    if not p.exists():
        return
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _find(share_id: str) -> dict[str, Any] | None:
    # Walk in reverse so the newest record for a given id wins if any
    # operator ever rewrites history. For a JSONL store of share links
    # this stays O(N) but N is bounded by retention; good enough until
    # we move it behind a real db.
    last: dict[str, Any] | None = None
    for rec in _iter_records():
        if rec.get("id") == share_id:
            last = rec
    return last


def _collapse_for_tenant(tenant_id: str) -> dict[str, dict[str, Any]]:
    """Replay the share log and return the latest record per id for tenant.

    Tombstones (records with deleted=True) win over their predecessors,
    so revoked shares disappear from the listing and become 404 on read.
    """
    out: dict[str, dict[str, Any]] = {}
    for rec in _iter_records():
        if rec.get("tenant_id") != tenant_id:
            continue
        sid = rec.get("id")
        if not sid:
            continue
        out[sid] = rec
    return out


def _is_deleted(rec: dict[str, Any] | None) -> bool:
    return bool(rec and rec.get("deleted"))


_SECONDS_PER_DAY = 86_400


def _expires_at_value(rec: dict[str, Any] | None) -> float:
    """Return ``expires_at`` as a float, treating 0/missing as no expiry."""
    if not rec:
        return 0.0
    try:
        return float(rec.get("expires_at") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _is_expired(rec: dict[str, Any] | None, now: float | None = None) -> bool:
    exp = _expires_at_value(rec)
    if exp <= 0:
        return False
    return (now if now is not None else time.time()) >= exp


def _resolve_expires_at(requested_days: int | None, now: float) -> float:
    """Translate a caller-supplied ``expires_in_days`` to an epoch second.

    Rules:
      * ``None`` (omitted) -> apply ``share_default_ttl_days``; 0 means
        no expiry.
      * ``0`` -> caller explicitly asked for a non-expiring link. We
        honour that unless ``share_max_ttl_days`` is set AND the
        workspace default is also non-zero, in which case we clamp to
        the max as a safety net for compliance-driven deployments.
      * positive int -> clamp to ``share_max_ttl_days`` and translate.
    """
    s = get_settings()
    default_days = int(s.share_default_ttl_days or 0)
    max_days = int(s.share_max_ttl_days or 0)
    if requested_days is None:
        days = default_days
    else:
        days = int(requested_days)
    if days <= 0:
        if requested_days == 0 and default_days > 0 and max_days > 0:
            days = max_days
        else:
            return 0.0
    if max_days > 0 and days > max_days:
        days = max_days
    return now + days * _SECONDS_PER_DAY


class ShareCreateBody(BaseModel):
    query_id: str
    elapsed_ms: int = 0
    count: int = 0
    results: list[MatchResult] = Field(default_factory=list)
    filename: str | None = None
    duration_sec: float | None = None
    note: str | None = Field(default=None, max_length=280)
    expires_in_days: int | None = Field(
        default=None,
        ge=0,
        le=3650,
        description=(
            "Link lifetime in days. Omit to use the workspace default "
            "(share_default_ttl_days). 0 requests a non-expiring link; "
            "the server clamps to share_max_ttl_days when a ceiling is "
            "set. Values above the ceiling are clamped silently."
        ),
    )


class ShareUpdateBody(BaseModel):
    """Partial update for an existing share. Today the human note and
    the link lifetime are editable. New optional fields can be added
    here without breaking older clients because every field is optional.
    """

    note: str | None = Field(default=None, max_length=280)
    expires_in_days: int | None = Field(
        default=None,
        ge=0,
        le=3650,
        description=(
            "Reset the link lifetime, measured from now. 0 removes the "
            "expiry (subject to share_max_ttl_days). Omit to leave the "
            "current expiry untouched."
        ),
    )


class ShareCreateResponse(BaseModel):
    id: str
    url_path: str  # client renders the absolute URL using window.location.origin
    expires_at: float = 0.0


class ShareListItem(BaseModel):
    id: str
    created_at: float
    query_id: str
    elapsed_ms: int
    count: int
    filename: str | None = None
    duration_sec: float | None = None
    note: str | None = None
    top_title: str | None = None
    top_artist: str | None = None
    top_score: float | None = None
    url_path: str
    expires_at: float = 0.0
    expired: bool = False


class ShareListResponse(BaseModel):
    shares: list[ShareListItem]
    total: int


class SharePublicResponse(BaseModel):
    id: str
    created_at: float
    query_id: str
    elapsed_ms: int
    count: int
    results: list[MatchResult]
    filename: str | None = None
    duration_sec: float | None = None
    note: str | None = None
    expires_at: float = 0.0
    embed_allowed_origins: list[str] = []


def _row_from_record(sid: str, rec: dict[str, Any], now: float) -> ShareListItem:
    results = rec.get("results") or []
    top = results[0] if results else None
    expires_at = _expires_at_value(rec)
    return ShareListItem(
        id=sid,
        created_at=float(rec.get("created_at") or 0.0),
        query_id=str(rec.get("query_id") or ""),
        elapsed_ms=int(rec.get("elapsed_ms") or 0),
        count=int(rec.get("count") or 0),
        filename=rec.get("filename"),
        duration_sec=rec.get("duration_sec"),
        note=rec.get("note"),
        top_title=(top or {}).get("title"),
        top_artist=(top or {}).get("artist"),
        top_score=(top or {}).get("score"),
        url_path=f"/r/{sid}",
        expires_at=expires_at,
        expired=expires_at > 0 and now >= expires_at,
    )


@router.post("/share", response_model=ShareCreateResponse, dependencies=[Depends(require_api_key)])
async def create_share(body: ShareCreateBody, request: Request) -> ShareCreateResponse:
    if not body.results:
        raise HTTPException(400, "cannot share an empty result set")
    if len(body.results) > 50:
        raise HTTPException(400, "too many results in one share (max 50)")
    tenant_id = getattr(request.state, "tenant_id", "anonymous")
    api_key_name = getattr(request.state, "api_key_name", "dev")
    share_id = _new_id()
    now = time.time()
    expires_at = _resolve_expires_at(body.expires_in_days, now)
    record = {
        "id": share_id,
        "created_at": now,
        "tenant_id": tenant_id,
        "api_key_name": api_key_name,
        "query_id": body.query_id,
        "elapsed_ms": int(body.elapsed_ms),
        "count": int(body.count or len(body.results)),
        "results": [r.model_dump() for r in body.results],
        "filename": body.filename,
        "duration_sec": body.duration_sec,
        "note": body.note,
        "expires_at": expires_at,
    }
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with _WRITE_LOCK:
        with _store_path().open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return ShareCreateResponse(
        id=share_id, url_path=f"/r/{share_id}", expires_at=expires_at
    )


@router.get(
    "/share",
    response_model=ShareListResponse,
    dependencies=[Depends(require_api_key)],
)
async def list_shares(request: Request) -> ShareListResponse:
    tenant_id = getattr(request.state, "tenant_id", "anonymous")
    collapsed = _collapse_for_tenant(tenant_id)
    now = time.time()
    items: list[ShareListItem] = []
    for sid, rec in collapsed.items():
        if _is_deleted(rec):
            continue
        items.append(_row_from_record(sid, rec, now))
    items.sort(key=lambda x: x.created_at, reverse=True)
    return ShareListResponse(shares=items, total=len(items))


@router.patch(
    "/share/{share_id}",
    response_model=ShareListItem,
    dependencies=[Depends(require_api_key)],
)
async def update_share(
    share_id: str, body: ShareUpdateBody, request: Request
) -> ShareListItem:
    if not share_id or len(share_id) > 64 or not share_id.isalnum():
        raise HTTPException(404, "not found")
    tenant_id = getattr(request.state, "tenant_id", "anonymous")
    rec = _find(share_id)
    if rec is None or _is_deleted(rec):
        raise HTTPException(404, "not found")
    if rec.get("tenant_id") != tenant_id:
        # Don't leak existence across tenants.
        raise HTTPException(404, "not found")
    # Build a full merged record so the append-only log keeps replaying
    # cleanly: _collapse_for_tenant always picks the newest record per id.
    merged = dict(rec)
    if body.note is not None:
        # Empty string is a valid clear; treat as removing the note.
        note = body.note.strip()
        merged["note"] = note if note else None
    now = time.time()
    if body.expires_in_days is not None:
        # Re-resolve against current settings so an admin lowering
        # share_max_ttl_days takes effect on every subsequent extend.
        merged["expires_at"] = _resolve_expires_at(body.expires_in_days, now)
    merged["updated_at"] = now
    line = json.dumps(merged, ensure_ascii=False, separators=(",", ":"))
    with _WRITE_LOCK, _store_path().open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return _row_from_record(share_id, merged, now)


@router.delete("/share/{share_id}", dependencies=[Depends(require_api_key)])
async def revoke_share(share_id: str, request: Request) -> dict[str, Any]:
    if not share_id or len(share_id) > 64 or not share_id.isalnum():
        raise HTTPException(404, "not found")
    tenant_id = getattr(request.state, "tenant_id", "anonymous")
    rec = _find(share_id)
    if rec is None or _is_deleted(rec):
        raise HTTPException(404, "not found")
    if rec.get("tenant_id") != tenant_id:
        # Don't leak existence: report as missing rather than 403.
        raise HTTPException(404, "not found")
    from ..dry_run import is_dry_run, preview
    if is_dry_run(request):
        return preview("share", share_id, tenant_id=tenant_id,
                       url_path=f"/r/{share_id}")
    tomb = {
        "id": share_id,
        "tenant_id": tenant_id,
        "deleted": True,
        "updated_at": time.time(),
    }
    line = json.dumps(tomb, ensure_ascii=False, separators=(",", ":"))
    with _WRITE_LOCK:
        with _store_path().open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return {"ok": True, "id": share_id}


@router.get("/share/{share_id}", response_model=SharePublicResponse)
async def get_share(share_id: str, request: Request) -> SharePublicResponse:
    if not share_id or len(share_id) > 64 or not share_id.isalnum():
        raise HTTPException(404, "not found")
    rec = _find(share_id)
    if rec is None or _is_deleted(rec):
        raise HTTPException(404, "not found")
    if _is_expired(rec):
        # 410 Gone communicates "this resource existed and is now
        # intentionally retired" so search engines and clients can
        # cache the deletion. We deliberately do not 404 here because
        # the owner can still extend the lifetime via PATCH.
        raise HTTPException(410, "share link expired")
    from .. import embed_origins as _eo
    tenant_id = str(rec.get("tenant_id") or "")
    allowed = [o.origin for o in _eo.list_origins(tenant_id)] if tenant_id else []
    if allowed:
        # If the request came from a browser via fetch(), the Origin
        # header is present. We do not block the JSON read itself
        # (the share remains publicly readable for crawlers, link
        # previews, and server-side renderers), but we do enforce
        # the allowlist when a browser is calling: a non-matching
        # Origin gets 403 so the JSON cannot be lifted into a hostile
        # page that then re-embeds the iframe.
        origin = request.headers.get("origin")
        if origin and not _eo.is_allowed(tenant_id, origin):
            raise HTTPException(status_code=403, detail="origin not permitted to read this share")
    return SharePublicResponse(
        id=rec["id"],
        created_at=float(rec.get("created_at") or 0.0),
        query_id=str(rec.get("query_id") or ""),
        elapsed_ms=int(rec.get("elapsed_ms") or 0),
        count=int(rec.get("count") or 0),
        results=[MatchResult(**r) for r in (rec.get("results") or [])],
        filename=rec.get("filename"),
        duration_sec=rec.get("duration_sec"),
        note=rec.get("note"),
        expires_at=_expires_at_value(rec),
        embed_allowed_origins=allowed,
    )
