from __future__ import annotations
import time
from fastapi import APIRouter, Request
from starlette.responses import PlainTextResponse

router = APIRouter()
_COUNTERS: dict[str, float] = {"requests_total": 0.0, "match_total": 0.0, "match_latency_sum_s": 0.0}
_START = time.time()


def inc(name: str, n: float = 1.0) -> None:
    _COUNTERS[name] = _COUNTERS.get(name, 0.0) + n


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(request: Request):
    st = request.app.state.clawhum
    lines = [
        "# HELP clawhum_uptime_seconds Process uptime",
        "# TYPE clawhum_uptime_seconds counter",
        f"clawhum_uptime_seconds {time.time() - _START:.1f}",
        "# HELP clawhum_index_vectors Total vectors in index",
        "# TYPE clawhum_index_vectors gauge",
        f"clawhum_index_vectors {st.index.size()}",
        "# HELP clawhum_index_tracks Total tracks in index",
        "# TYPE clawhum_index_tracks gauge",
        f"clawhum_index_tracks {len(st.tracks)}",
    ]
    for k, v in _COUNTERS.items():
        lines.append(f"# TYPE clawhum_{k} counter")
        lines.append(f"clawhum_{k} {v}")
    return "\n".join(lines) + "\n"
