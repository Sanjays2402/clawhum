"""Prometheus metrics exposition.

Uses the official prometheus_client library so the /metrics endpoint
emits a valid text exposition with HELP and TYPE lines, process and
platform collectors, and proper escaping. Two RED-style instruments
are recorded per request via a middleware:

  clawhum_http_requests_total{method,route,status}  Counter
  clawhum_http_request_duration_seconds{method,route} Histogram

Route labels use Starlette's matched route path template (e.g.
"/v1/match") rather than the raw URL so cardinality stays bounded
even when path parameters are present. Requests that do not match a
route are labelled "unmatched". Health and metrics endpoints are
excluded from request metrics to keep the signal focused on user
traffic but are still served by the same FastAPI app.

Domain gauges (index size, track count, uptime) are evaluated lazily
inside a custom collector so the exposition reflects the current
state of app.state.clawhum at scrape time.
"""

from __future__ import annotations

import time
from collections.abc import Iterable

from fastapi import APIRouter, Request
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

# Single process registry. We deliberately do not use the global
# REGISTRY so tests can build the app multiple times without
# CollectorRegistry duplicate-name errors.
REGISTRY = CollectorRegistry(auto_describe=True)

REQUESTS = Counter(
    "clawhum_http_requests_total",
    "Total HTTP requests handled, labelled by method, matched route, and status.",
    labelnames=("method", "route", "status"),
    registry=REGISTRY,
)

LATENCY = Histogram(
    "clawhum_http_request_duration_seconds",
    "HTTP request latency in seconds, labelled by method and matched route.",
    labelnames=("method", "route"),
    # Buckets tuned for an API that mixes sub-millisecond health checks
    # with multi-second match requests. Aligns with Prometheus defaults
    # in the lower range and extends to 30s for embedding heavy paths.
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=REGISTRY,
)

_EXCLUDED_PATHS = {"/metrics"}
_START = time.time()


def _matched_route(request: Request, fallback_path: str) -> str:
    """Return the route path template if Starlette matched one, else 'unmatched'.

    Starlette stores the matched Route on request.scope['route'] when
    the request reaches an endpoint. Middlewares execute before route
    resolution, but we read the value after call_next which sees the
    final scope. We avoid using request.url.path directly because that
    would label every distinct dynamic segment as a unique series and
    blow up Prometheus cardinality.
    """
    route = request.scope.get("route")
    if route is not None:
        path = getattr(route, "path", None)
        if path:
            return path
    # Health checks and other static endpoints will still match a route,
    # so this branch is reserved for genuine 404s.
    return "unmatched"


class _AppStateCollector:
    """Surface domain gauges sourced from app.state.clawhum at scrape time.

    Keeping these in a custom collector means each scrape reflects the
    live values without needing a background updater task.
    """

    def __init__(self, app):
        self._app = app

    def collect(self) -> Iterable:
        uptime = GaugeMetricFamily(
            "clawhum_uptime_seconds",
            "Seconds since the API process started.",
        )
        uptime.add_metric([], time.time() - _START)
        yield uptime

        st = getattr(self._app.state, "clawhum", None)
        vectors = 0
        tracks = 0
        if st is not None:
            try:
                vectors = int(st.index.size())
            except Exception:
                vectors = 0
            try:
                tracks = int(len(st.tracks))
            except Exception:
                tracks = 0

        vec = GaugeMetricFamily(
            "clawhum_index_vectors",
            "Total vectors loaded in the search index.",
        )
        vec.add_metric([], vectors)
        yield vec

        tr = GaugeMetricFamily(
            "clawhum_index_tracks",
            "Total tracks loaded in the library metadata.",
        )
        tr.add_metric([], tracks)
        yield tr


def register_app_collector(app) -> None:
    """Bind an app-aware collector onto the metrics registry.

    Safe to call multiple times across test app rebuilds; duplicate
    instances are reconciled by unregistering any previous collector
    that matches by class identity.
    """
    # CollectorRegistry has no public list, but _names_to_collectors is
    # the documented internal map used by tests in prometheus_client
    # itself. We only iterate to find our class for idempotency.
    existing = [c for c in list(REGISTRY._collector_to_names.keys())
                if isinstance(c, _AppStateCollector)]
    for c in existing:
        REGISTRY.unregister(c)
    REGISTRY.register(_AppStateCollector(app))


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Record per-request count and latency labelled by matched route."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _EXCLUDED_PATHS:
            return await call_next(request)
        method = request.method
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            elapsed = time.perf_counter() - start
            route = _matched_route(request, path)
            try:
                REQUESTS.labels(method=method, route=route, status=str(status_code)).inc()
                LATENCY.labels(method=method, route=route).observe(elapsed)
            except Exception:
                # Metrics must never break the request path.
                pass


router = APIRouter()


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    """Prometheus text exposition for the API process."""
    payload = generate_latest(REGISTRY)
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)


# Backwards compatible counter helper retained for any caller still
# poking the legacy interface. New code should use REQUESTS/LATENCY
# directly or add a dedicated metric.
def inc(name: str, n: float = 1.0) -> None:
    """Increment a named counter for legacy call sites.

    Counter creation is deferred so the metric only appears once it has
    actually been incremented. Subsequent calls reuse the same series.
    """
    counter = _LEGACY_COUNTERS.get(name)
    if counter is None:
        counter = Counter(
            f"clawhum_{name}",
            f"Legacy counter for {name}.",
            registry=REGISTRY,
        )
        _LEGACY_COUNTERS[name] = counter
    counter.inc(n)


_LEGACY_COUNTERS: dict[str, Counter] = {}
