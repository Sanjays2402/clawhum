from __future__ import annotations

from collections import Counter

from clawhum_core.settings import get_settings
from clawhum_library.feedback import read_feedback, record_feedback
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth import require_api_key, require_roles
from ..schemas import FeedbackBody
from ..tenant import current_tenant_id, scope_rows

router = APIRouter(tags=["feedback"], dependencies=[Depends(require_api_key)])


@router.post("/feedback")
async def feedback(body: FeedbackBody, request: Request):
    s = get_settings()
    tenant_id = current_tenant_id(request)
    record_feedback(
        s.feedback_path,
        body.query_id,
        body.track_id,
        body.score,
        body.vote,
        tenant_id=tenant_id,
    )
    return {"ok": True, "tenant_id": tenant_id}


@router.get("/feedback", dependencies=[Depends(require_roles("reader"))])
async def list_feedback(
    request: Request,
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    vote: int | None = Query(default=None, ge=-1, le=1),
    track_id: str | None = Query(default=None, max_length=128),
):
    """Return the current tenant's feedback rows with a small summary.

    Newest rows first. Used by the web review queue to show every vote a
    user has submitted and to export confirmed / rejected pairs as a
    triplet-loss training corpus.
    """
    s = get_settings()
    tenant_id = current_tenant_id(request)
    try:
        all_rows = read_feedback(s.feedback_path)
    except (OSError, ValueError) as exc:  # corrupted file, permissions, etc.
        raise HTTPException(status_code=500, detail=f"feedback log unreadable: {exc}")

    scoped = scope_rows(all_rows, tenant_id)
    if vote is not None:
        scoped = [r for r in scoped if int(r.get("vote", 0)) == vote]
    if track_id:
        scoped = [r for r in scoped if r.get("track_id") == track_id]

    scoped.sort(key=lambda r: float(r.get("ts", 0.0)), reverse=True)
    total = len(scoped)
    page = scoped[offset : offset + limit]

    votes = Counter(int(r.get("vote", 0)) for r in scoped)
    unique_queries = len({r.get("query_id") for r in scoped if r.get("query_id")})
    unique_tracks = len({r.get("track_id") for r in scoped if r.get("track_id")})

    return {
        "tenant_id": tenant_id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "summary": {
            "confirm": int(votes.get(1, 0)),
            "reject": int(votes.get(-1, 0)),
            "neutral": int(votes.get(0, 0)),
            "unique_queries": unique_queries,
            "unique_tracks": unique_tracks,
        },
        "rows": page,
    }
