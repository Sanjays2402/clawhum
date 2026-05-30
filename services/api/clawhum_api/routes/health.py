from __future__ import annotations

import time

from clawhum_core.version import __version__
from fastapi import APIRouter, Request, Response, status

from ..schemas import HealthResponse, ReadinessResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request):
    """Liveness style summary. Returns 200 as long as the process is up
    and the app object has booted. Used by Helm livenessProbe."""
    st = getattr(request.app.state, "clawhum", None)
    if st is None:
        # Process is up but lifespan has not finished. Still 200 so the
        # pod is not killed during slow boots; readiness gates traffic.
        return HealthResponse(
            ok=True,
            version=__version__,
            embedder="unbooted",
            index_backend="unbooted",
            tracks=0,
            vectors=0,
        )
    return HealthResponse(
        ok=True,
        version=__version__,
        embedder=st.embedder.__class__.__name__,
        index_backend=st.index.__class__.__name__,
        tracks=len(st.tracks),
        vectors=st.index.size(),
    )


@router.get("/live")
async def live():
    """Pure liveness probe. Always 200 while the event loop is responsive."""
    return {"live": True}


def _readiness_checks(request: Request) -> ReadinessResponse:
    """Run real readiness checks and return structured result.

    A pod is ready when:
      - lifespan startup finished (app.state.clawhum exists)
      - embedder object is constructed
      - index object is loaded (size() is callable, even if 0 vectors)
      - api key registry has been resolved (open or with at least one key)
    """
    checks: dict[str, str] = {}
    ok = True

    st = getattr(request.app.state, "clawhum", None)
    if st is None:
        checks["boot"] = "fail: lifespan not complete"
        ok = False
        return ReadinessResponse(ready=False, version=__version__, checks=checks, vectors=0)
    checks["boot"] = "ok"

    try:
        emb_name = st.embedder.__class__.__name__
        checks["embedder"] = f"ok:{emb_name}"
    except Exception as exc:  # pragma: no cover - defensive
        checks["embedder"] = f"fail:{exc!r}"
        ok = False

    vectors = 0
    try:
        vectors = int(st.index.size())
        checks["index"] = f"ok:{st.index.__class__.__name__}:{vectors}"
    except Exception as exc:
        checks["index"] = f"fail:{exc!r}"
        ok = False

    try:
        from ..api_keys import get_registry

        reg = get_registry()
        if reg.is_open():
            checks["auth"] = "ok:open"
        else:
            n = len(reg.by_secret)
            if n == 0:
                checks["auth"] = "fail: no API keys configured and auth not open"
                ok = False
            else:
                checks["auth"] = f"ok:{n}-keys"
    except Exception as exc:
        checks["auth"] = f"fail:{exc!r}"
        ok = False

    return ReadinessResponse(ready=ok, version=__version__, checks=checks, vectors=vectors)


@router.get("/ready", response_model=ReadinessResponse)
async def ready(request: Request, response: Response):
    """Real readiness check. Returns 503 if any dependency is not ready
    so Kubernetes readiness probes correctly remove the pod from the
    Service endpoint list during slow boots or degraded states."""
    result = _readiness_checks(request)
    if not result.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result


@router.get("/startup")
async def startup(request: Request, response: Response):
    """Startup probe target. Mirrors /ready but exists as a separate
    endpoint so Kubernetes startupProbe can use longer failure budgets
    without affecting readiness semantics."""
    started = getattr(request.app.state, "clawhum", None) is not None
    if not started:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"started": started, "ts": int(time.time())}
