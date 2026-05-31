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


class ShareCreateBody(BaseModel):
    query_id: str
    elapsed_ms: int = 0
    count: int = 0
    results: list[MatchResult] = Field(default_factory=list)
    filename: str | None = None
    duration_sec: float | None = None
    note: str | None = Field(default=None, max_length=280)


class ShareUpdateBody(BaseModel):
    """Partial update for an existing share. Today only the human note
    is editable. New optional fields can be added here without breaking
    older clients because every field is optional.
    """

    note: str | None = Field(default=None, max_length=280)


class ShareCreateResponse(BaseModel):
    id: str
    url_path: str  # client renders the absolute URL using window.location.origin


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


@router.post("/share", response_model=ShareCreateResponse, dependencies=[Depends(require_api_key)])
async def create_share(body: ShareCreateBody, request: Request) -> ShareCreateResponse:
    if not body.results:
        raise HTTPException(400, "cannot share an empty result set")
    if len(body.results) > 50:
        raise HTTPException(400, "too many results in one share (max 50)")
    tenant_id = getattr(request.state, "tenant_id", "anonymous")
    api_key_name = getattr(request.state, "api_key_name", "dev")
    share_id = _new_id()
    record = {
        "id": share_id,
        "created_at": time.time(),
        "tenant_id": tenant_id,
        "api_key_name": api_key_name,
        "query_id": body.query_id,
        "elapsed_ms": int(body.elapsed_ms),
        "count": int(body.count or len(body.results)),
        "results": [r.model_dump() for r in body.results],
        "filename": body.filename,
        "duration_sec": body.duration_sec,
        "note": body.note,
    }
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with _WRITE_LOCK:
        with _store_path().open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return ShareCreateResponse(id=share_id, url_path=f"/r/{share_id}")


@router.get(
    "/share",
    response_model=ShareListResponse,
    dependencies=[Depends(require_api_key)],
)
async def list_shares(request: Request) -> ShareListResponse:
    tenant_id = getattr(request.state, "tenant_id", "anonymous")
    collapsed = _collapse_for_tenant(tenant_id)
    items: list[ShareListItem] = []
    for sid, rec in collapsed.items():
        if _is_deleted(rec):
            continue
        results = rec.get("results") or []
        top = results[0] if results else None
        items.append(
            ShareListItem(
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
            )
        )
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
    merged["updated_at"] = time.time()
    line = json.dumps(merged, ensure_ascii=False, separators=(",", ":"))
    with _WRITE_LOCK:
        with _store_path().open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    results = merged.get("results") or []
    top = results[0] if results else None
    return ShareListItem(
        id=share_id,
        created_at=float(merged.get("created_at") or 0.0),
        query_id=str(merged.get("query_id") or ""),
        elapsed_ms=int(merged.get("elapsed_ms") or 0),
        count=int(merged.get("count") or 0),
        filename=merged.get("filename"),
        duration_sec=merged.get("duration_sec"),
        note=merged.get("note"),
        top_title=(top or {}).get("title"),
        top_artist=(top or {}).get("artist"),
        top_score=(top or {}).get("score"),
        url_path=f"/r/{share_id}",
    )


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
async def get_share(share_id: str) -> SharePublicResponse:
    if not share_id or len(share_id) > 64 or not share_id.isalnum():
        raise HTTPException(404, "not found")
    rec = _find(share_id)
    if rec is None or _is_deleted(rec):
        raise HTTPException(404, "not found")
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
    )
