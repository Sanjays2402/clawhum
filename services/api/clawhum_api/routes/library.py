from __future__ import annotations
import mimetypes
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from fastapi.responses import FileResponse

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
