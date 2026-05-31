"""Per-tenant match history.

Server-side persistence for run history so it survives device switches
and localStorage clears. The web client also keeps a local cache, but
this endpoint is the source of truth for an authenticated user.

POST   /history             save a match (returns id)
GET    /history             list saved matches (newest first, paginated)
GET    /history/{id}        fetch one
PATCH  /history/{id}        rename / retag
DELETE /history/{id}        delete one

Storage is a JSONL file at settings.history_path, scoped per tenant.
Reads only return rows owned by the calling tenant. Public reads are
intentionally not supported here; share links exist for that.
"""

from __future__ import annotations

import csv
import io
import json
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from clawhum_core.settings import get_settings
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..auth import require_api_key
from ..schemas import MatchResult

router = APIRouter(tags=["history"])

_WRITE_LOCK = Lock()
_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_ID_LEN = 12
_MAX_TAGS = 16
_MAX_TAG_LEN = 32
_MAX_NAME = 120
_MAX_RESULTS = 50


def _new_id() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_ID_LEN))


def _valid_id(s: str) -> bool:
    return bool(s) and len(s) <= 64 and s.isalnum()


def _store_path() -> Path:
    p = getattr(get_settings(), "history_path", Path("./data/history.jsonl"))
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
    """Return latest record-per-id for the given tenant.

    Deleted records are emitted as tombstones (deleted=True) so we
    honor deletes without rewriting the file. PATCH writes append a
    full updated row; the newest one wins.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for rec in _iter_records():
        if rec.get("tenant_id") != tenant_id:
            continue
        rid = rec.get("id")
        if not isinstance(rid, str):
            continue
        by_id[rid] = rec
    # drop tombstones
    return {k: v for k, v in by_id.items() if not v.get("deleted")}


def _append(record: dict[str, Any]) -> None:
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with _WRITE_LOCK:
        with _store_path().open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _normalize_tags(tags: list[str] | None) -> list[str]:
    if not tags:
        return []
    seen: list[str] = []
    for t in tags:
        if not isinstance(t, str):
            continue
        v = t.strip().lower()[:_MAX_TAG_LEN]
        if v and v not in seen:
            seen.append(v)
        if len(seen) >= _MAX_TAGS:
            break
    return seen


class HistoryCreateBody(BaseModel):
    query_id: str = Field(min_length=1, max_length=128)
    elapsed_ms: int = 0
    count: int = 0
    results: list[MatchResult] = Field(default_factory=list)
    filename: str | None = Field(default=None, max_length=240)
    duration_sec: float | None = None
    name: str | None = Field(default=None, max_length=_MAX_NAME)
    tags: list[str] = Field(default_factory=list)


class HistoryPatchBody(BaseModel):
    name: str | None = Field(default=None, max_length=_MAX_NAME)
    tags: list[str] | None = None
    starred: bool | None = None


class HistoryItem(BaseModel):
    id: str
    created_at: float
    updated_at: float
    query_id: str
    elapsed_ms: int
    count: int
    results: list[MatchResult]
    filename: str | None = None
    duration_sec: float | None = None
    name: str | None = None
    tags: list[str] = Field(default_factory=list)
    starred: bool = False


class HistoryListResponse(BaseModel):
    items: list[HistoryItem]
    total: int
    limit: int
    offset: int


class HistoryCreateResponse(BaseModel):
    id: str


def _to_item(rec: dict[str, Any]) -> HistoryItem:
    return HistoryItem(
        id=str(rec["id"]),
        created_at=float(rec.get("created_at") or 0.0),
        updated_at=float(rec.get("updated_at") or rec.get("created_at") or 0.0),
        query_id=str(rec.get("query_id") or ""),
        elapsed_ms=int(rec.get("elapsed_ms") or 0),
        count=int(rec.get("count") or 0),
        results=[MatchResult(**r) for r in (rec.get("results") or [])],
        filename=rec.get("filename"),
        duration_sec=rec.get("duration_sec"),
        name=rec.get("name"),
        tags=list(rec.get("tags") or []),
        starred=bool(rec.get("starred") or False),
    )


@router.post(
    "/history",
    response_model=HistoryCreateResponse,
    dependencies=[Depends(require_api_key)],
)
async def create_history(body: HistoryCreateBody, request: Request) -> HistoryCreateResponse:
    if not body.results:
        raise HTTPException(400, "cannot save an empty result set")
    if len(body.results) > _MAX_RESULTS:
        raise HTTPException(400, f"too many results in one entry (max {_MAX_RESULTS})")
    tenant_id = getattr(request.state, "tenant_id", "anonymous")
    api_key_name = getattr(request.state, "api_key_name", "dev")
    now = time.time()
    hid = _new_id()
    rec = {
        "id": hid,
        "tenant_id": tenant_id,
        "api_key_name": api_key_name,
        "created_at": now,
        "updated_at": now,
        "query_id": body.query_id,
        "elapsed_ms": int(body.elapsed_ms),
        "count": int(body.count or len(body.results)),
        "results": [r.model_dump() for r in body.results],
        "filename": body.filename,
        "duration_sec": body.duration_sec,
        "name": (body.name or "").strip() or None,
        "tags": _normalize_tags(body.tags),
        "starred": False,
    }
    _append(rec)
    return HistoryCreateResponse(id=hid)


@router.get(
    "/history",
    response_model=HistoryListResponse,
    dependencies=[Depends(require_api_key)],
)
async def list_history(
    request: Request,
    q: str = Query(default="", max_length=120),
    tag: str = Query(default="", max_length=_MAX_TAG_LEN),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sort: str = Query(default="recent", pattern="^(recent|oldest|name|results|top_score)$"),
    starred: bool = Query(default=False),
) -> HistoryListResponse:
    tenant_id = getattr(request.state, "tenant_id", "anonymous")
    rows = list(_collapse(tenant_id).values())
    needle = q.strip().lower()
    tag_needle = tag.strip().lower()
    if starred:
        rows = [r for r in rows if bool(r.get("starred"))]
    if needle:
        def hit(r: dict[str, Any]) -> bool:
            if needle in (r.get("name") or "").lower():
                return True
            if needle in (r.get("filename") or "").lower():
                return True
            for res in r.get("results") or []:
                if needle in str(res.get("title", "")).lower():
                    return True
                if needle in str(res.get("artist", "")).lower():
                    return True
            return False
        rows = [r for r in rows if hit(r)]
    if tag_needle:
        rows = [r for r in rows if tag_needle in (r.get("tags") or [])]

    def _top_score(r: dict[str, Any]) -> float:
        results = r.get("results") or []
        if not results:
            return 0.0
        try:
            return float(max((float(x.get("score") or 0.0)) for x in results))
        except (TypeError, ValueError):
            return 0.0

    if sort == "oldest":
        rows.sort(key=lambda r: float(r.get("created_at") or 0.0))
    elif sort == "name":
        rows.sort(key=lambda r: ((r.get("name") or r.get("filename") or "").lower(), -float(r.get("created_at") or 0.0)))
    elif sort == "results":
        rows.sort(key=lambda r: (int(r.get("count") or 0), float(r.get("created_at") or 0.0)), reverse=True)
    elif sort == "top_score":
        rows.sort(key=lambda r: (_top_score(r), float(r.get("created_at") or 0.0)), reverse=True)
    else:  # recent
        rows.sort(key=lambda r: float(r.get("created_at") or 0.0), reverse=True)
    total = len(rows)
    page = rows[offset : offset + limit]
    return HistoryListResponse(
        items=[_to_item(r) for r in page],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/history/export", dependencies=[Depends(require_api_key)])
async def export_history(
    request: Request,
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    q: str = Query(default="", max_length=120),
    tag: str = Query(default="", max_length=_MAX_TAG_LEN),
    starred: bool = Query(default=False),
) -> Response:
    """Download the caller's full history matching the given filters.

    Returns every record (not paginated). CSV flattens to one row per
    candidate result; JSON returns a list of full history items.
    """
    tenant_id = getattr(request.state, "tenant_id", "anonymous")
    rows = list(_collapse(tenant_id).values())
    needle = q.strip().lower()
    tag_needle = tag.strip().lower()
    if needle:
        def hit(r: dict[str, Any]) -> bool:
            if needle in (r.get("name") or "").lower():
                return True
            if needle in (r.get("filename") or "").lower():
                return True
            for res in r.get("results") or []:
                if needle in str(res.get("title", "")).lower():
                    return True
                if needle in str(res.get("artist", "")).lower():
                    return True
            return False
        rows = [r for r in rows if hit(r)]
    if tag_needle:
        rows = [r for r in rows if tag_needle in (r.get("tags") or [])]
    if starred:
        rows = [r for r in rows if bool(r.get("starred"))]
    rows.sort(key=lambda r: float(r.get("created_at") or 0.0), reverse=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if format == "json":
        items = [_to_item(r).model_dump() for r in rows]
        payload = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "tenant_id": tenant_id,
            "count": len(items),
            "items": items,
        }
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        return Response(
            content=body,
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="clawhum-history-{stamp}.json"',
                "Cache-Control": "no-store",
            },
        )

    # CSV: one row per candidate result. Empty history still emits headers.
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "history_id",
        "created_at_iso",
        "query_id",
        "name",
        "filename",
        "duration_sec",
        "elapsed_ms",
        "tags",
        "candidate_rank",
        "track_id",
        "title",
        "artist",
        "album",
        "score",
    ])
    for r in rows:
        created = float(r.get("created_at") or 0.0)
        created_iso = datetime.fromtimestamp(created, timezone.utc).isoformat() if created else ""
        tags_s = ",".join(r.get("tags") or [])
        results = r.get("results") or []
        if not results:
            w.writerow([
                r.get("id", ""), created_iso, r.get("query_id", ""),
                r.get("name") or "", r.get("filename") or "",
                r.get("duration_sec") if r.get("duration_sec") is not None else "",
                int(r.get("elapsed_ms") or 0), tags_s,
                "", "", "", "", "", "",
            ])
            continue
        for idx, res in enumerate(results, start=1):
            w.writerow([
                r.get("id", ""), created_iso, r.get("query_id", ""),
                r.get("name") or "", r.get("filename") or "",
                r.get("duration_sec") if r.get("duration_sec") is not None else "",
                int(r.get("elapsed_ms") or 0), tags_s,
                idx,
                str(res.get("track_id") or ""),
                str(res.get("title") or ""),
                str(res.get("artist") or ""),
                str(res.get("album") or ""),
                res.get("score") if res.get("score") is not None else "",
            ])
    return Response(
        content=buf.getvalue().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="clawhum-history-{stamp}.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.get(
    "/history/{hid}",
    response_model=HistoryItem,
    dependencies=[Depends(require_api_key)],
)
async def get_history(hid: str, request: Request) -> HistoryItem:
    if not _valid_id(hid):
        raise HTTPException(404, "not found")
    tenant_id = getattr(request.state, "tenant_id", "anonymous")
    rec = _collapse(tenant_id).get(hid)
    if rec is None:
        raise HTTPException(404, "not found")
    return _to_item(rec)


@router.patch(
    "/history/{hid}",
    response_model=HistoryItem,
    dependencies=[Depends(require_api_key)],
)
async def patch_history(hid: str, body: HistoryPatchBody, request: Request) -> HistoryItem:
    if not _valid_id(hid):
        raise HTTPException(404, "not found")
    tenant_id = getattr(request.state, "tenant_id", "anonymous")
    rec = _collapse(tenant_id).get(hid)
    if rec is None:
        raise HTTPException(404, "not found")
    updated = dict(rec)
    if body.name is not None:
        updated["name"] = body.name.strip() or None
    if body.tags is not None:
        updated["tags"] = _normalize_tags(body.tags)
    if body.starred is not None:
        updated["starred"] = bool(body.starred)
    updated["updated_at"] = time.time()
    _append(updated)
    return _to_item(updated)


@router.delete("/history/{hid}", dependencies=[Depends(require_api_key)])
async def delete_history(hid: str, request: Request) -> dict[str, Any]:
    if not _valid_id(hid):
        raise HTTPException(404, "not found")
    tenant_id = getattr(request.state, "tenant_id", "anonymous")
    rec = _collapse(tenant_id).get(hid)
    if rec is None:
        raise HTTPException(404, "not found")
    from ..dry_run import is_dry_run, preview
    if is_dry_run(request):
        return preview("history", hid, tenant_id=tenant_id,
                       query=rec.get("query"), starred=bool(rec.get("starred")))
    tomb = {
        "id": hid,
        "tenant_id": tenant_id,
        "deleted": True,
        "updated_at": time.time(),
    }
    _append(tomb)
    return {"ok": True, "id": hid}
