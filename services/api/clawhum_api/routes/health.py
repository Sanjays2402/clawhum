from __future__ import annotations
from fastapi import APIRouter, Request
from clawhum_core.version import __version__
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request):
    st = request.app.state.clawhum
    return HealthResponse(
        ok=True, version=__version__,
        embedder=st.embedder.__class__.__name__,
        index_backend=st.index.__class__.__name__,
        tracks=len(st.tracks), vectors=st.index.size(),
    )


@router.get("/ready")
async def ready():
    return {"ready": True}
