"""Per-tenant usage and quota introspection.

Powers the in-app usage meter and upgrade CTA. Returns rolling
minute / day / month counts for the caller, the configured free-tier
monthly quota, and a 30 day daily breakdown for sparklines.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..auth import require_api_key
from ..tenant import current_tenant_id
from ..usage import recent_counts

router = APIRouter(tags=["usage"])


@router.get("/usage", dependencies=[Depends(require_api_key)])
async def usage(request: Request) -> dict:
    tenant = current_tenant_id(request)
    return recent_counts(tenant)
