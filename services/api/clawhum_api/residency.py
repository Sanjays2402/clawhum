"""Workspace data residency enforcement.

Resolves the tenant id off the inbound ``x-api-key`` (the same way
``SimpleRateLimit`` does) without waiting for FastAPI dependencies to
run, then rejects mutating requests whose workspace is pinned to a
region that does not match this node's configured ``CLAWHUM_REGION``.

Read-only requests (``GET``, ``HEAD``, ``OPTIONS``) are allowed through
in any region so dashboards and audit log viewers stay usable when an
operator is debugging from outside the data plane region. The mutating
gate covers ``POST``, ``PUT``, ``PATCH`` and ``DELETE`` for every route
mounted in the app, both unversioned and ``/v1`` prefixed, so a new
mutating endpoint cannot accidentally bypass residency by being added
to a router we forgot to wrap.

Configuration:

* ``CLAWHUM_REGION`` (default ``unset``) the region this node is in.
* ``CLAWHUM_RESIDENCY_ENFORCEMENT`` (default ``true``) master switch.

When the node region is ``unset`` no check fires, which keeps existing
single region installs unaffected. When a workspace's pin is ``unset``
or ``enforce`` is false the check also no-ops, so opt-in stays per
tenant. When both sides are set, mismatch returns 451 with a JSON body
naming the expected region so the client can route to the correct
endpoint.
"""

from __future__ import annotations

from collections.abc import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import pat_store, residency_store
from .api_keys import ANON_TENANT_ID, get_registry

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Paths that must work in any region (health/metrics, identity probes,
# and the residency admin surface itself so an admin locked out of the
# wrong region can still read or repair their pin).
_BYPASS_PREFIXES: tuple[str, ...] = (
    "/health",
    "/ready",
    "/metrics",
    "/residency",
    "/v1/residency",
)


def _bypass(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _BYPASS_PREFIXES)


def _resolve_tenant(request: Request) -> str:
    api_key = request.headers.get("x-api-key", "")
    if not api_key:
        return ANON_TENANT_ID
    registry = get_registry()
    entry = registry.lookup(api_key)
    if entry is not None:
        return entry.tenant_id or ANON_TENANT_ID
    if pat_store.looks_like_pat(api_key):
        pat = pat_store.lookup_by_secret(api_key)
        if pat is not None:
            return pat.tenant_id or ANON_TENANT_ID
    return ANON_TENANT_ID


class ResidencyMiddleware(BaseHTTPMiddleware):
    """Block mutating requests that violate the workspace region pin."""

    def __init__(self, app, *, node_region: str, enforcement: bool) -> None:
        super().__init__(app)
        region = (node_region or "unset").lower()
        if region not in residency_store.VALID_REGIONS:
            region = "unset"
        self.node_region = region
        self.enforcement = bool(enforcement)

    async def dispatch(self, request: Request, call_next):
        if (
            not self.enforcement
            or self.node_region == "unset"
            or request.method not in MUTATING_METHODS
            or _bypass(request.url.path)
        ):
            response = await call_next(request)
            response.headers["X-Data-Region"] = self.node_region
            # Surface workspace pin on reads too so dashboards can show
            # the current region without an extra round trip.
            if not _bypass(request.url.path):
                tenant_id = _resolve_tenant(request)
                pin = residency_store.get(tenant_id)
                if pin.region != "unset":
                    response.headers["X-Workspace-Region"] = pin.region
            return response

        tenant_id = _resolve_tenant(request)
        pin = residency_store.get(tenant_id)
        if pin.enforce and pin.region != "unset" and pin.region != self.node_region:
            return JSONResponse(
                {
                    "detail": "data residency violation",
                    "tenant_region": pin.region,
                    "node_region": self.node_region,
                    "code": "residency_mismatch",
                },
                status_code=451,
                headers={
                    "X-Data-Region": self.node_region,
                    "X-Workspace-Region": pin.region,
                },
            )
        response = await call_next(request)
        response.headers["X-Data-Region"] = self.node_region
        if pin.region != "unset":
            response.headers["X-Workspace-Region"] = pin.region
        return response


def bypass_prefixes() -> Iterable[str]:
    """Expose bypass list so tests can pin behaviour."""
    return _BYPASS_PREFIXES
