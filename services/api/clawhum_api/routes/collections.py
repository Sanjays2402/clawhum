"""User-curated collections of shared matches.

A "collection" is an ordered, named bundle of result snapshots a user
wants to group and share as one URL (think: a mini setlist, a research
panel, a 'top humming guesses from the demo' page). The owner CRUDs
them with their API key; the GET-by-id endpoint is public so anyone
with the link can view, mirroring the /share contract.

Storage is the same dependency-free JSONL pattern used by share.py and
webhooks.py: append-only writes, replay-with-tombstones on read,
tenant-scoped at write, public on read.
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

router = APIRouter(tags=["collections"])

_WRITE_LOCK = Lock()
_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_ID_LEN = 12
_MAX_ITEMS = 50
_MAX_TITLE = 80
_MAX_NOTE = 280


def _new_id() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_ID_LEN))


def _store_path() -> Path:
    p = get_settings().collections_path
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


def _collapse() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rec in _iter_records():
        cid = rec.get("id")
        if not cid:
            continue
        out[cid] = rec
    return out


def _find(cid: str) -> dict[str, Any] | None:
    return _collapse().get(cid)


def _is_deleted(rec: dict[str, Any] | None) -> bool:
    return bool(rec and rec.get("deleted"))


def _valid_id(cid: str) -> bool:
    return bool(cid) and len(cid) <= 64 and cid.isalnum()


def _append(rec: dict[str, Any]) -> None:
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
    with _WRITE_LOCK:
        with _store_path().open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


class CollectionItem(BaseModel):
    label: str = Field(default="", max_length=_MAX_TITLE)
    results: list[MatchResult] = Field(default_factory=list)
    query_id: str | None = None
    elapsed_ms: int = 0
    filename: str | None = None
    duration_sec: float | None = None


class CollectionBody(BaseModel):
    title: str = Field(min_length=1, max_length=_MAX_TITLE)
    note: str | None = Field(default=None, max_length=_MAX_NOTE)
    items: list[CollectionItem] = Field(default_factory=list)


class CollectionCreateResponse(BaseModel):
    id: str
    url_path: str


class CollectionSummary(BaseModel):
    id: str
    created_at: float
    updated_at: float
    title: str
    note: str | None = None
    item_count: int
    url_path: str


class CollectionListResponse(BaseModel):
    collections: list[CollectionSummary]
    total: int


class CollectionPublicResponse(BaseModel):
    id: str
    created_at: float
    updated_at: float
    title: str
    note: str | None = None
    items: list[CollectionItem]


def _validate_body(body: CollectionBody) -> None:
    if len(body.items) > _MAX_ITEMS:
        raise HTTPException(400, f"too many items in one collection (max {_MAX_ITEMS})")
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "title is required")


def _summarize(rec: dict[str, Any]) -> CollectionSummary:
    items = rec.get("items") or []
    return CollectionSummary(
        id=str(rec["id"]),
        created_at=float(rec.get("created_at") or 0.0),
        updated_at=float(rec.get("updated_at") or rec.get("created_at") or 0.0),
        title=str(rec.get("title") or ""),
        note=rec.get("note"),
        item_count=len(items),
        url_path=f"/c/{rec['id']}",
    )


@router.post(
    "/collections",
    response_model=CollectionCreateResponse,
    dependencies=[Depends(require_api_key)],
)
async def create_collection(
    body: CollectionBody, request: Request
) -> CollectionCreateResponse:
    _validate_body(body)
    tenant_id = getattr(request.state, "tenant_id", "anonymous")
    api_key_name = getattr(request.state, "api_key_name", "dev")
    cid = _new_id()
    now = time.time()
    record = {
        "id": cid,
        "created_at": now,
        "updated_at": now,
        "tenant_id": tenant_id,
        "api_key_name": api_key_name,
        "title": body.title.strip(),
        "note": (body.note or None),
        "items": [item.model_dump() for item in body.items],
    }
    _append(record)
    return CollectionCreateResponse(id=cid, url_path=f"/c/{cid}")


@router.get(
    "/collections",
    response_model=CollectionListResponse,
    dependencies=[Depends(require_api_key)],
)
async def list_collections(request: Request) -> CollectionListResponse:
    tenant_id = getattr(request.state, "tenant_id", "anonymous")
    items: list[CollectionSummary] = []
    for cid, rec in _collapse().items():
        if rec.get("tenant_id") != tenant_id:
            continue
        if _is_deleted(rec):
            continue
        items.append(_summarize(rec))
    items.sort(key=lambda x: x.updated_at, reverse=True)
    return CollectionListResponse(collections=items, total=len(items))


@router.patch(
    "/collections/{cid}",
    response_model=CollectionSummary,
    dependencies=[Depends(require_api_key)],
)
async def update_collection(
    cid: str, body: CollectionBody, request: Request
) -> CollectionSummary:
    if not _valid_id(cid):
        raise HTTPException(404, "not found")
    _validate_body(body)
    tenant_id = getattr(request.state, "tenant_id", "anonymous")
    rec = _find(cid)
    if rec is None or _is_deleted(rec):
        raise HTTPException(404, "not found")
    if rec.get("tenant_id") != tenant_id:
        raise HTTPException(404, "not found")
    now = time.time()
    new_rec = {
        **rec,
        "title": body.title.strip(),
        "note": (body.note or None),
        "items": [item.model_dump() for item in body.items],
        "updated_at": now,
    }
    _append(new_rec)
    return _summarize(new_rec)


@router.delete(
    "/collections/{cid}",
    dependencies=[Depends(require_api_key)],
)
async def delete_collection(cid: str, request: Request) -> dict[str, Any]:
    if not _valid_id(cid):
        raise HTTPException(404, "not found")
    tenant_id = getattr(request.state, "tenant_id", "anonymous")
    rec = _find(cid)
    if rec is None or _is_deleted(rec):
        raise HTTPException(404, "not found")
    if rec.get("tenant_id") != tenant_id:
        raise HTTPException(404, "not found")
    tomb = {
        "id": cid,
        "tenant_id": tenant_id,
        "deleted": True,
        "updated_at": time.time(),
    }
    _append(tomb)
    return {"ok": True, "id": cid}


@router.get(
    "/collections/{cid}",
    response_model=CollectionPublicResponse,
)
async def get_collection(cid: str) -> CollectionPublicResponse:
    if not _valid_id(cid):
        raise HTTPException(404, "not found")
    rec = _find(cid)
    if rec is None or _is_deleted(rec):
        raise HTTPException(404, "not found")
    return CollectionPublicResponse(
        id=str(rec["id"]),
        created_at=float(rec.get("created_at") or 0.0),
        updated_at=float(rec.get("updated_at") or rec.get("created_at") or 0.0),
        title=str(rec.get("title") or ""),
        note=rec.get("note"),
        items=[CollectionItem(**it) for it in (rec.get("items") or [])],
    )
