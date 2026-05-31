"""Workspace data retention policy administration.

Per-tenant policy that limits how long history, feedback, audit, and
webhook delivery records live. Admins can view and update the policy
for their own workspace, and trigger an enforcement sweep that hard
deletes rows older than the configured TTL. Cross-tenant data is
never visible or mutable from this surface; the storage layer scopes
on tenant_id and the enforcement helper double checks each row.
"""

from __future__ import annotations

import hashlib
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import require_api_key, require_mfa, require_roles
from ..tenant import current_tenant
from .. import retention

router = APIRouter(
    tags=["retention"],
    prefix="/retention",
    dependencies=[Depends(require_api_key)],
)


class PolicyOut(BaseModel):
    tenant_id: str
    history_days: int
    feedback_days: int
    audit_days: int
    webhook_deliveries_days: int
    updated_at: float
    updated_by: str


class PolicyUpdate(BaseModel):
    history_days: int = Field(default=0, ge=0, le=3650)
    feedback_days: int = Field(default=0, ge=0, le=3650)
    audit_days: int = Field(default=0, ge=0, le=3650)
    webhook_deliveries_days: int = Field(default=0, ge=0, le=3650)


class EnforceResponse(BaseModel):
    tenant_id: str
    removed: dict[str, int]
    ran_at: float


def _to_out(p: retention.RetentionPolicy) -> PolicyOut:
    return PolicyOut(
        tenant_id=p.tenant_id,
        history_days=p.history_days,
        feedback_days=p.feedback_days,
        audit_days=p.audit_days,
        webhook_deliveries_days=p.webhook_deliveries_days,
        updated_at=p.updated_at,
        updated_by=p.updated_by,
    )


def _actor_digest(api_key: str | None) -> str:
    if not api_key:
        return "anonymous"
    return "key:" + hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


@router.get(
    "",
    response_model=PolicyOut,
    dependencies=[Depends(require_roles("admin"))],
)
async def get_my_policy(tenant_id: str = Depends(current_tenant)) -> PolicyOut:
    return _to_out(retention.get_policy(tenant_id))


@router.put(
    "",
    response_model=PolicyOut,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def update_my_policy(
    body: PolicyUpdate,
    tenant_id: str = Depends(current_tenant),
    x_api_key: str = Header(default=""),
) -> PolicyOut:
    pol = retention.set_policy(
        tenant_id,
        history_days=body.history_days,
        feedback_days=body.feedback_days,
        audit_days=body.audit_days,
        webhook_deliveries_days=body.webhook_deliveries_days,
        updated_by=_actor_digest(x_api_key or None),
    )
    return _to_out(pol)


@router.post(
    "/enforce",
    response_model=EnforceResponse,
    dependencies=[Depends(require_roles("admin")), Depends(require_mfa())],
)
async def enforce_now(
    request: Request,
    dry_run: bool = False,
    tenant_id: str = Depends(current_tenant),
) -> EnforceResponse:
    """Sweep all categories for the calling tenant.

    ``dry_run=true`` reports what would be removed without rewriting
    any file, matching the convention used elsewhere in the API.
    """
    if dry_run:
        # Count rows that would be removed without touching disk.
        from pathlib import Path
        import json as _json

        pol = retention.get_policy(tenant_id)
        if pol.is_empty():
            return EnforceResponse(tenant_id=tenant_id, removed={c: 0 for c in retention.POLICY_FIELDS}, ran_at=time.time())
        from clawhum_core.settings import get_settings as _gs
        s = _gs()
        now = time.time()
        out = {c: 0 for c in retention.POLICY_FIELDS}
        for category in retention.POLICY_FIELDS:
            days = pol.days_for(category)
            if days <= 0:
                continue
            path = Path(getattr(s, retention.CATEGORIES[category]))
            if not path.exists():
                continue
            cutoff = now - (days * 86400.0)
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                row_tenant = row.get("tenant_id") or "default"
                if row_tenant != tenant_id:
                    continue
                ts = retention._row_timestamp(row)
                if ts == 0.0:
                    continue
                if ts < cutoff:
                    out[category] += 1
        return EnforceResponse(tenant_id=tenant_id, removed=out, ran_at=time.time())
    try:
        removed = retention.enforce_policy(tenant_id)
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(500, f"retention sweep failed: {exc}") from exc
    return EnforceResponse(tenant_id=tenant_id, removed=removed, ran_at=time.time())
