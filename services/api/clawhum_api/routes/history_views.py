"""Saved history views.

A "view" is a named filter combination over the user's history
(search query, tag, sort, starred-only). The user saves one from the
history page and restores it later with a single click.

Storage is a JSONL file at settings.history_views_path, scoped per
tenant. PATCH and DELETE append updated rows; the newest record per
id wins (delete tombstone removes it from the listing). This mirrors
the history route storage so the two surfaces age the same way.

Endpoints
    GET    /history/views        list saved views (newest first)
    POST   /history/views        create a view (returns id)
    PATCH  /history/views/{id}   rename or update filters
    DELETE /history/views/{id}   delete one

Routes are mounted at the root and again under /v1 by app.py so they
sit on the same stable public surface as history itself.
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from clawhum_core.settings import get_settings
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import require_api_key

router = APIRouter(tags=["history-views"])

_WRITE_LOCK = Lock()
_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_ID_LEN = 12
_MAX_NAME = 80
_MAX_VIEWS = 50  # per tenant; cheap safeguard against runaway clients

SortLiteral = Literal["recent", "oldest", "name", "results", "top_score"]


def _new_id() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_ID_LEN))


def _valid_id(s: str) -> bool:
    return bool(s) and len(s) <= 64 and s.isalnum()


def _store_path() -> Path:
    p = getattr(get_settings(), "history_views_path", Path("./data/history_views.jsonl"))
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


def _collapse(tenant_id: str) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for rec in _iter_records():
        if rec.get("tenant_id") != tenant_id:
            continue
        rid = rec.get("id")
        if not isinstance(rid, str):
            continue
        by_id[rid] = rec
    return {k: v for k, v in by_id.items() if not v.get("deleted")}


def _append(record: dict[str, Any]) -> None:
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with _WRITE_LOCK:
        with _store_path().open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


class ViewFilters(BaseModel):
    q: str = Field(default="", max_length=120)
    tag: str = Field(default="", max_length=32)
    sort: SortLiteral = "recent"
    starred: bool = False


class ViewCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=_MAX_NAME)
    filters: ViewFilters = Field(default_factory=ViewFilters)


class ViewPatchBody(BaseModel):
    name: str | None = Field(default=None, max_length=_MAX_NAME)
    filters: ViewFilters | None = None


class ViewItem(BaseModel):
    id: str
    name: str
    filters: ViewFilters
    created_at: float
    updated_at: float


class ViewListResponse(BaseModel):
    items: list[ViewItem]
    total: int


class ViewCreateResponse(BaseModel):
    id: str


def _to_item(rec: dict[str, Any]) -> ViewItem:
    f = rec.get("filters") or {}
    return ViewItem(
        id=str(rec["id"]),
        name=str(rec.get("name") or ""),
        filters=ViewFilters(
            q=str(f.get("q") or ""),
            tag=str(f.get("tag") or ""),
            sort=str(f.get("sort") or "recent"),  # type: ignore[arg-type]
            starred=bool(f.get("starred") or False),
        ),
        created_at=float(rec.get("created_at") or 0.0),
        updated_at=float(rec.get("updated_at") or rec.get("created_at") or 0.0),
    )


def _normalize_name(name: str) -> str:
    return " ".join(name.split())[:_MAX_NAME]


@router.get(
    "/history/views",
    response_model=ViewListResponse,
    dependencies=[Depends(require_api_key)],
)
async def list_views(request: Request) -> ViewListResponse:
    tenant_id = getattr(request.state, "tenant_id", "anonymous")
    rows = list(_collapse(tenant_id).values())
    rows.sort(key=lambda r: float(r.get("updated_at") or r.get("created_at") or 0.0), reverse=True)
    items = [_to_item(r) for r in rows]
    return ViewListResponse(items=items, total=len(items))


@router.post(
    "/history/views",
    response_model=ViewCreateResponse,
    dependencies=[Depends(require_api_key)],
)
async def create_view(body: ViewCreateBody, request: Request) -> ViewCreateResponse:
    name = _normalize_name(body.name)
    if not name:
        raise HTTPException(400, "name is required")
    tenant_id = getattr(request.state, "tenant_id", "anonymous")
    existing = _collapse(tenant_id)
    if len(existing) >= _MAX_VIEWS:
        raise HTTPException(409, f"too many saved views (max {_MAX_VIEWS})")
    # Reject duplicate names (case-insensitive) so the sidebar stays tidy.
    lname = name.lower()
    for rec in existing.values():
        if str(rec.get("name") or "").strip().lower() == lname:
            raise HTTPException(409, "a view with that name already exists")
    now = time.time()
    vid = _new_id()
    rec = {
        "id": vid,
        "tenant_id": tenant_id,
        "name": name,
        "filters": body.filters.model_dump(),
        "created_at": now,
        "updated_at": now,
    }
    _append(rec)
    return ViewCreateResponse(id=vid)


@router.patch(
    "/history/views/{view_id}",
    response_model=ViewItem,
    dependencies=[Depends(require_api_key)],
)
async def patch_view(view_id: str, body: ViewPatchBody, request: Request) -> ViewItem:
    if not _valid_id(view_id):
        raise HTTPException(400, "invalid id")
    tenant_id = getattr(request.state, "tenant_id", "anonymous")
    rows = _collapse(tenant_id)
    current = rows.get(view_id)
    if current is None:
        raise HTTPException(404, "view not found")
    name = current.get("name")
    if body.name is not None:
        name = _normalize_name(body.name)
        if not name:
            raise HTTPException(400, "name cannot be empty")
        lname = name.lower()
        for rid, rec in rows.items():
            if rid == view_id:
                continue
            if str(rec.get("name") or "").strip().lower() == lname:
                raise HTTPException(409, "a view with that name already exists")
    filters = current.get("filters") or {}
    if body.filters is not None:
        filters = body.filters.model_dump()
    updated = {
        **current,
        "name": name,
        "filters": filters,
        "updated_at": time.time(),
    }
    _append(updated)
    return _to_item(updated)


@router.delete(
    "/history/views/{view_id}",
    dependencies=[Depends(require_api_key)],
)
async def delete_view(view_id: str, request: Request) -> dict[str, bool]:
    if not _valid_id(view_id):
        raise HTTPException(400, "invalid id")
    tenant_id = getattr(request.state, "tenant_id", "anonymous")
    rows = _collapse(tenant_id)
    if view_id not in rows:
        raise HTTPException(404, "view not found")
    tombstone = {
        "id": view_id,
        "tenant_id": tenant_id,
        "deleted": True,
        "updated_at": time.time(),
    }
    _append(tombstone)
    return {"deleted": True}
