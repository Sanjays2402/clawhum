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


class ShareCreateBody(BaseModel):
    query_id: str
    elapsed_ms: int = 0
    count: int = 0
    results: list[MatchResult] = Field(default_factory=list)
    filename: str | None = None
    duration_sec: float | None = None
    note: str | None = Field(default=None, max_length=280)


class ShareCreateResponse(BaseModel):
    id: str
    url_path: str  # client renders the absolute URL using window.location.origin


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


@router.get("/share/{share_id}", response_model=SharePublicResponse)
async def get_share(share_id: str) -> SharePublicResponse:
    if not share_id or len(share_id) > 64 or not share_id.isalnum():
        raise HTTPException(404, "not found")
    rec = _find(share_id)
    if rec is None:
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
