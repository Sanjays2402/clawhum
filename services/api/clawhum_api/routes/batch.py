"""Batch matching: upload a zip of audio clips, get one results file back.

Why this exists: real users have folders full of hums or song
fragments. Running them through the UI one at a time is fine for a
first taste, but the second visit is always "can I just point it at
my whole folder?". This route accepts a single .zip whose entries are
audio files and returns either a JSON array or a CSV file with the
top match per clip.

Design notes:
- Synchronous on purpose. The matcher is CPU-bound but fast, and
  most realistic batches (tens to low hundreds of clips) finish well
  inside a normal HTTP timeout. Adding a job queue here would push
  this into a refactor; we stay inside the existing FastAPI surface.
- Per-clip failures (bad codec, empty file) do not fail the batch.
  Each row carries an ``error`` field so a customer can see which
  clip needs cleaning without re-running the rest.
- Zip entry size is capped to defend against zip bombs.
- Output is streamed back so the browser sees a real download.
"""
from __future__ import annotations

import csv
import io
import time
import uuid
import zipfile
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from clawhum_audio.io import load_audio
from clawhum_core.settings import get_settings
from clawhum_match.matcher import Matcher

from ..auth import require_api_key
from ..tenant import current_tenant_id
from .. import match_duration

router = APIRouter(tags=["batch"])

# Caps. We want this to be useful for real folders without becoming a
# DoS surface. 100 clips and 50 MiB per clip uncompressed is enough
# for a serious humming session and small enough that the matcher
# finishes in a reasonable HTTP window.
MAX_CLIPS = 100
MAX_CLIP_BYTES = 50 * 1024 * 1024
MAX_ZIP_BYTES = 200 * 1024 * 1024
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".aiff", ".aif"}


def _is_audio_name(name: str) -> bool:
    lower = name.lower()
    # Skip macOS metadata directories and dotfiles. They are never
    # the user's audio and would just clutter the result file.
    if "__MACOSX" in name or name.startswith(".") or "/." in name:
        return False
    for ext in AUDIO_EXTS:
        if lower.endswith(ext):
            return True
    return False


@router.post("/batch", dependencies=[Depends(require_api_key)])
async def batch(
    request: Request,
    archive: UploadFile = File(..., description="Zip file of audio clips"),
    top_k: int | None = Form(default=None),
    threshold: float | None = Form(default=None),
    format: Literal["json", "csv"] = Form(default="json"),
):
    settings = get_settings()
    state = request.app.state.clawhum
    if not state.tracks:
        raise HTTPException(400, "index is empty; run reindex first")

    blob = await archive.read()
    if not blob:
        raise HTTPException(400, "empty upload")
    if len(blob) > MAX_ZIP_BYTES:
        raise HTTPException(
            413, f"archive too large; max {MAX_ZIP_BYTES // (1024 * 1024)} MiB"
        )
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as e:
        raise HTTPException(400, f"not a valid zip archive: {e}") from e

    entries = [
        info for info in zf.infolist()
        if not info.is_dir() and _is_audio_name(info.filename)
    ]
    if not entries:
        raise HTTPException(
            400,
            "zip contained no audio files (expected one of "
            + ", ".join(sorted(AUDIO_EXTS))
            + ")",
        )
    if len(entries) > MAX_CLIPS:
        raise HTTPException(413, f"too many clips; max {MAX_CLIPS} per batch")

    matcher = Matcher(state.embedder, state.index, state.tracks)
    batch_id = str(uuid.uuid4())
    eff_top_k = top_k or settings.top_k
    eff_threshold = threshold if threshold is not None else settings.threshold
    # Per-workspace decoded-duration cap, applied per clip below.
    duration_cap = match_duration.max_duration_sec(current_tenant_id(request))

    rows: list[dict] = []
    for info in entries:
        if info.file_size > MAX_CLIP_BYTES:
            rows.append({
                "filename": info.filename,
                "error": f"clip exceeds {MAX_CLIP_BYTES // (1024 * 1024)} MiB cap",
                "matches": [],
                "elapsed_ms": 0,
            })
            continue
        try:
            with zf.open(info) as handle:
                raw = handle.read(MAX_CLIP_BYTES + 1)
            if len(raw) > MAX_CLIP_BYTES:
                rows.append({
                    "filename": info.filename,
                    "error": "clip exceeds size cap",
                    "matches": [],
                    "elapsed_ms": 0,
                })
                continue
            x, sr = load_audio(io.BytesIO(raw), target_sr=state.embedder.sr)
        except Exception as e:  # noqa: BLE001 - report decode error per row
            rows.append({
                "filename": info.filename,
                "error": f"decode failed: {e}",
                "matches": [],
                "elapsed_ms": 0,
            })
            continue

        # Per-workspace decoded-duration cap. 0 = no cap (default).
        if duration_cap and sr > 0:
            dur = float(len(x)) / float(sr)
            if dur > duration_cap:
                rows.append({
                    "filename": info.filename,
                    "error": (
                        f"clip duration {dur:.2f}s exceeds workspace"
                        f" cap of {duration_cap}s"
                    ),
                    "matches": [],
                    "elapsed_ms": 0,
                })
                continue

        t0 = time.perf_counter()
        matches = matcher.match(x, sr, top_k=eff_top_k, threshold=eff_threshold)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        rows.append({
            "filename": info.filename,
            "error": None,
            "elapsed_ms": elapsed_ms,
            "matches": [
                {
                    "track_id": m.track.id,
                    "title": m.track.title,
                    "artist": m.track.artist,
                    "score": float(m.score),
                }
                for m in matches
            ],
        })

    if format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "filename", "rank", "track_id", "title", "artist",
            "score", "elapsed_ms", "error",
        ])
        for row in rows:
            if row["error"] or not row["matches"]:
                writer.writerow([
                    row["filename"], "", "", "", "",
                    "", row["elapsed_ms"], row["error"] or "no matches",
                ])
                continue
            for rank, m in enumerate(row["matches"], start=1):
                writer.writerow([
                    row["filename"], rank, m["track_id"], m["title"],
                    m["artist"], f"{m['score']:.6f}", row["elapsed_ms"], "",
                ])
        body = buf.getvalue().encode("utf-8")
        return StreamingResponse(
            io.BytesIO(body),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="clawhum-batch-{batch_id}.csv"',
                "X-Batch-Id": batch_id,
                "X-Batch-Count": str(len(rows)),
            },
        )

    return {
        "batch_id": batch_id,
        "count": len(rows),
        "ok": sum(1 for r in rows if not r["error"]),
        "failed": sum(1 for r in rows if r["error"]),
        "results": rows,
    }
