from __future__ import annotations
import mimetypes
from pathlib import Path
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel

from clawhum_core.settings import get_settings
from clawhum_indexer.build import build_index, IndexerOptions
from ..auth import require_api_key, require_roles
from ..schemas import ReindexBody, StatsResponse
from ..state import AppState

_AUDIO_MIMES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
}

router = APIRouter(tags=["library"], dependencies=[Depends(require_api_key)])


@router.get("/stats", response_model=StatsResponse)
async def stats(request: Request):
    st = request.app.state.clawhum
    return StatsResponse(
        tracks=len(st.tracks),
        vectors=st.index.size(),
        dim=st.embedder.dim,
        backend=st.index.__class__.__name__,
    )


@router.post(
    "/reindex",
    response_model=dict,
    dependencies=[Depends(require_roles("writer"))],
)
async def reindex(body: ReindexBody, request: Request, bg: BackgroundTasks):
    s = get_settings()
    lib = Path(body.library_path) if body.library_path else s.library_path
    if body.library_path and not lib.exists():
        raise HTTPException(400, f"library path does not exist: {lib}")

    def _run():
        result = build_index(IndexerOptions(
            library_path=lib, spotify_playlist=body.spotify_playlist, use_clap=body.use_clap,
        ))
        request.app.state.clawhum = AppState.boot(prefer_clap=body.use_clap)
        return result

    bg.add_task(_run)
    return {"started": True, "library_path": str(lib), "spotify_playlist": body.spotify_playlist}


class TrackSummary(BaseModel):
    id: str
    title: str
    artist: str = ""
    album: str = ""
    duration_s: float = 0.0
    source: str = "local"
    tempo_bpm: Optional[float] = None
    key: Optional[str] = None
    preview_url: Optional[str] = None
    artwork_url: Optional[str] = None
    has_audio: bool = False


class TracksListResponse(BaseModel):
    items: list[TrackSummary]
    total: int
    limit: int
    offset: int


def _track_to_summary(t) -> TrackSummary:
    raw = getattr(t, "path", None)
    has_audio = False
    if raw:
        try:
            has_audio = Path(raw).is_file()
        except OSError:
            has_audio = False
    return TrackSummary(
        id=t.id, title=t.title or "", artist=t.artist or "", album=t.album or "",
        duration_s=float(t.duration_s or 0.0), source=t.source or "local",
        tempo_bpm=t.tempo_bpm, key=t.key,
        preview_url=t.preview_url, artwork_url=t.artwork_url,
        has_audio=has_audio,
    )


@router.get("/tracks", response_model=TracksListResponse)
async def list_tracks(
    request: Request,
    q: str = Query("", description="case-insensitive substring match across id/title/artist/album"),
    source: Optional[str] = Query(None, description="filter by source, e.g. local, spotify"),
    sort: Literal["title", "artist", "duration", "id"] = Query("title"),
    order: Literal["asc", "desc"] = Query("asc"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List indexed tracks. Server-backed catalog browser."""
    state = request.app.state.clawhum
    tracks = list(state.tracks.values())
    if q:
        needle = q.lower()
        tracks = [
            t for t in tracks
            if needle in (t.id or "").lower()
            or needle in (t.title or "").lower()
            or needle in (t.artist or "").lower()
            or needle in (t.album or "").lower()
        ]
    if source:
        tracks = [t for t in tracks if (t.source or "") == source]
    key_fns = {
        "title": lambda t: (t.title or "").lower(),
        "artist": lambda t: (t.artist or "").lower(),
        "duration": lambda t: float(t.duration_s or 0.0),
        "id": lambda t: t.id,
    }
    tracks.sort(key=key_fns[sort], reverse=(order == "desc"))
    total = len(tracks)
    page = tracks[offset:offset + limit]
    return TracksListResponse(
        items=[_track_to_summary(t) for t in page],
        total=total, limit=limit, offset=offset,
    )


@router.get("/track/{track_id}", response_model=TrackSummary)
async def get_track(track_id: str, request: Request):
    state = request.app.state.clawhum
    t = state.tracks.get(track_id)
    if t is None:
        raise HTTPException(404, "unknown track_id")
    return _track_to_summary(t)


@router.get("/track/{track_id}/audio")
async def track_audio(track_id: str, request: Request):
    """Stream the reference audio file for a track in the index.

    This is what powers the "reference" waveform and the in-browser A/B
    player on the match detail page. We only serve files that are still
    present in the in-memory track catalogue (so requests can't be used
    to probe arbitrary paths) and that resolve underneath the configured
    library root.
    """
    state = request.app.state.clawhum
    track = state.tracks.get(track_id)
    if track is None:
        raise HTTPException(404, "unknown track_id")
    raw_path = getattr(track, "path", None)
    if not raw_path:
        raise HTTPException(404, "track has no local audio file")
    p = Path(raw_path).resolve()
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "audio file missing on disk")
    # Confine to the configured library root when one is set.
    s = get_settings()
    lib_root = getattr(s, "library_path", None)
    if lib_root:
        try:
            p.relative_to(Path(lib_root).resolve())
        except ValueError:
            raise HTTPException(403, "audio path outside library root")
    media_type = _AUDIO_MIMES.get(p.suffix.lower()) or mimetypes.guess_type(str(p))[0] or "application/octet-stream"
    return FileResponse(
        str(p),
        media_type=media_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "Content-Disposition": f'inline; filename="{p.name}"',
        },
    )
