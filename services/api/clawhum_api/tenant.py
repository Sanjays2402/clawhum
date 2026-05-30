"""Multi-tenant scoping helpers.

Every authenticated request carries a tenant_id resolved from the API
key registry. This module centralises three concerns:

1. Reading the current tenant off the FastAPI request state with a
   safe default so unauthenticated paths (health, metrics) do not blow
   up.
2. A FastAPI dependency, current_tenant, that routes can declare to
   pull the tenant id without reaching into request.state directly.
3. A pure helper that filters row dicts by tenant_id. Storage layers
   stay storage agnostic; the API layer is responsible for tagging
   rows on write and scoping reads. This keeps the JSONL based stores
   honest without bolting on a database just to add tenancy.

Tenant ids are validated and lowercased upstream in api_keys.py so by
the time they reach this module they are already in the safe alphabet.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import structlog
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from .api_keys import ANON_TENANT_ID


def current_tenant_id(request: Request) -> str:
    """Return the resolved tenant id, or the anonymous bucket."""
    return getattr(request.state, "tenant_id", ANON_TENANT_ID) or ANON_TENANT_ID


async def current_tenant(request: Request) -> str:
    """FastAPI dependency form of current_tenant_id."""
    return current_tenant_id(request)


def scope_rows(rows: Iterable[dict[str, Any]], tenant_id: str) -> list[dict[str, Any]]:
    """Return only rows whose tenant_id matches.

    Rows without a tenant_id are treated as legacy data and surfaced
    only to the default tenant so historical entries written before
    multi tenancy was wired up are not orphaned forever.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        row_tenant = row.get("tenant_id")
        if row_tenant == tenant_id or row_tenant is None and tenant_id == "default":
            out.append(row)
    return out


class TenantScopeMiddleware(BaseHTTPMiddleware):
    """Bind the resolved tenant id into structlog contextvars.

    Auth runs as a per-route dependency, so the tenant id is only known
    after the route's dependencies execute. This middleware therefore
    binds the tenant id after call_next returns, ensuring response
    serialisation and any deferred logging carry the tenant tag. For
    requests that never authenticate (health, metrics) the binding is a
    no op.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        tenant = getattr(request.state, "tenant_id", None)
        if tenant:
            structlog.contextvars.bind_contextvars(tenant_id=tenant)
            response.headers["x-tenant-id"] = tenant
        return response
