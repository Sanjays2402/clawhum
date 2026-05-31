"""Pitch contour endpoints used by the match detail page to overlay
the user's query melody against the reference track's matched segment.
"""
from __future__ import annotations
import io
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from clawhum_core.settings import get_settings
from clawhum_audio.io import load_audio
from clawhum_audio.pitch import extract_pitch
from ..auth import require_api_key

router = APIRouter(tags=["pitch"], dependencies=[Depends(require_api_key)])

# Max query duration we will accept for an interactive pitch call. Anything
# beyond this is almost certainly a mis-use; cap to keep CPU bounded.
_MAX_DURATION_SEC = 30.0
# Hard cap on returned contour points; the UI does not need more.
_MAX_POINTS = 240


@router.post("/pitch")
async def pitch_from_upload(
    audio: UploadFile = File(...),
    start_sec: float | None = Form(default=None),
    duration_sec: float | None = Form(default=None),
):
    blob = await audio.read()
    if not blob:
        raise HTTPException(400, "empty upload")
    try:
        x, sr = load_audio(io.BytesIO(blob), target_sr=22050)
    except Exception as e:
        raise HTTPException(400, f"could not decode audio: {e}") from e
    total = x.size / sr
    if total > _MAX_DURATION_SEC and duration_sec is None:
        # Clamp instead of erroring; pyin on long files is slow but valid.
        duration_sec = _MAX_DURATION_SEC
    contour = extract_pitch(
        x, sr,
        start_sec=start_sec,
        duration_sec=duration_sec,
        max_points=_MAX_POINTS,
    )
    return contour.to_dict()


@router.get("/track/{track_id}/pitch")
async def track_pitch(
    track_id: str,
    request: Request,
    segment_index: int = 0,
    window: float = 1.0,
):
    """Return a pitch contour for the matched segment of a known track.

    ``segment_index`` is the integer second offset the matcher records on
    the result; ``window`` extends that by N seconds (default 1 s). Path
    resolution mirrors the /track/{id}/audio endpoint: the file must be
    in the in-memory catalogue and underneath the library root.
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
    s = get_settings()
    lib_root = getattr(s, "library_path", None)
    if lib_root:
        try:
            p.relative_to(Path(lib_root).resolve())
        except ValueError:
            raise HTTPException(403, "audio path outside library root")
    if window <= 0 or window > _MAX_DURATION_SEC:
        raise HTTPException(400, f"window must be in (0, {_MAX_DURATION_SEC}]")
    if segment_index < 0:
        raise HTTPException(400, "segment_index must be >= 0")

    try:
        x, sr = load_audio(p, target_sr=22050)
    except Exception as e:
        raise HTTPException(500, f"could not decode reference audio: {e}") from e
    contour = extract_pitch(
        x, sr,
        start_sec=float(segment_index),
        duration_sec=float(window),
        max_points=_MAX_POINTS,
    )
    return {
        "track_id": track_id,
        "segment_index": segment_index,
        "window": window,
        **contour.to_dict(),
    }
