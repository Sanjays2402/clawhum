from __future__ import annotations
import io
import time
import uuid
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Request

from clawhum_core.settings import get_settings
from clawhum_match.matcher import Matcher
from clawhum_audio.io import load_audio
from ..auth import require_api_key
from ..schemas import MatchResponse, MatchResult
from ..tenant import current_tenant_id
from . import webhooks as webhooks_routes

router = APIRouter(tags=["match"])


@router.post("/match", response_model=MatchResponse, dependencies=[Depends(require_api_key)])
async def match(
    request: Request,
    audio: UploadFile = File(...),
    top_k: int | None = Form(default=None),
    threshold: float | None = Form(default=None),
):
    s = get_settings()
    state = request.app.state.clawhum
    if not state.tracks:
        raise HTTPException(400, "index is empty; run reindex first")

    blob = await audio.read()
    if not blob:
        raise HTTPException(400, "empty upload")
    try:
        x, sr = load_audio(io.BytesIO(blob), target_sr=state.embedder.sr)
    except Exception as e:
        raise HTTPException(400, f"could not decode audio: {e}") from e

    matcher = Matcher(state.embedder, state.index, state.tracks)
    t0 = time.perf_counter()
    matches = matcher.match(
        x, sr,
        top_k=top_k or s.top_k,
        threshold=threshold if threshold is not None else s.threshold,
    )
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    response = MatchResponse(
        query_id=str(uuid.uuid4()),
        elapsed_ms=elapsed_ms,
        count=len(matches),
        results=[MatchResult(
            track_id=m.track.id, title=m.track.title, artist=m.track.artist,
            album=m.track.album, score=m.score, segment_index=m.segment_index,
            preview_url=m.track.preview_url, artwork_url=m.track.artwork_url,
            source=m.track.source, tempo_bpm=m.track.tempo_bpm,
        ) for m in matches],
    )
    # Best-effort outbound notification to user-registered webhooks. We never
    # block the response on receivers; deliveries fan out as background tasks.
    try:
        tenant_id = current_tenant_id(request)
        await webhooks_routes.dispatch_event(
            tenant_id,
            webhooks_routes.EVENT_MATCH_COMPLETED,
            response.model_dump(),
        )
    except Exception:
        pass
    return response
