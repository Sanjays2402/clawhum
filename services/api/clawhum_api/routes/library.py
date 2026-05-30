from __future__ import annotations
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks

from clawhum_core.settings import get_settings
from clawhum_indexer.build import build_index, IndexerOptions
from ..auth import require_api_key, require_roles
from ..schemas import ReindexBody, StatsResponse
from ..state import AppState

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
