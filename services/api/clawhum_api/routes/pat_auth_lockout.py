"""Per-IP brute-force lockout administration for PAT authentication.

Workspace admins can:

* GET ``/admin/pat-auth-lockout`` to read the current global thresholds
  and the list of IPs currently locked out for failed personal-access
  -token auth attempts associated with this workspace.
* DELETE ``/admin/pat-auth-lockout/{ip}`` to force-unlock a single IP.
  This is MFA-gated because clearing a lock weakens a brute-force
  defense and we want both an audit trail and step-up auth on the
  action.

Tenant scoped: an admin only sees locks whose most recent failure
was associated with their workspace (or no workspace at all, for the
"unknown PAT secret" case). Cross-tenant locks stay invisible.
"""

from __future__ import annotations

import ipaddress

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from clawhum_core.settings import get_settings

from .. import pat_auth_lockout
from ..audit import write_event
from ..auth import require_admin_with_mfa, require_roles
from ..tenant import current_tenant_id

router = APIRouter(tags=["pat-auth-lockout"], prefix="/admin/pat-auth-lockout")


class LockoutSettings(BaseModel):
    threshold: int = Field(
        description=(
            "Failed PAT auth attempts within the window from the same "
            "IP that trip a lock. 0 disables the lockout."
        )
    )
    window_seconds: int
    cooldown_seconds: int


class LockEntry(BaseModel):
    ip: str
    failures: int
    locked: bool
    locked_until: float
    retry_after: int
    last_tenant_id: str = ""


class LockoutOverview(BaseModel):
    settings: LockoutSettings
    locks: list[LockEntry]


class UnlockBody(BaseModel):
    reason: str = Field(
        default="",
        max_length=500,
        description="Free text recorded in the audit log next to the unlock.",
    )


def _actor_id(request: Request) -> str:
    return (
        getattr(request.state, "api_key_name", "")
        or getattr(request.state, "pat_id", "")
        or "unknown"
    )


def _settings_view() -> LockoutSettings:
    s = get_settings()
    return LockoutSettings(
        threshold=int(s.pat_auth_lockout_threshold),
        window_seconds=int(s.pat_auth_lockout_window_seconds),
        cooldown_seconds=int(s.pat_auth_lockout_cooldown_seconds),
    )


def _validate_ip(ip: str) -> str:
    raw = (ip or "").strip()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ip is required",
        )
    if len(raw) > 64 or any(c.isspace() for c in raw):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ip is malformed",
        )
    # Accept either a valid IP literal or an opaque token (the ip
    # field carries whatever upstream the trusted-proxy resolver
    # produced, which is normally an IPv4 or IPv6 string but in
    # test harnesses or unusual deployments may be a hostname).
    try:
        ipaddress.ip_address(raw)
    except ValueError:
        # Allow common safe-ish identifier characters only.
        allowed = set(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789.:_-[]"
        )
        if any(c not in allowed for c in raw):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ip contains disallowed characters",
            )
    return raw


@router.get(
    "",
    response_model=LockoutOverview,
    dependencies=[Depends(require_roles("reader"))],
)
async def get_overview(request: Request) -> LockoutOverview:
    tenant = current_tenant_id(request)
    locks = [
        LockEntry(**st.to_dict()) for st in pat_auth_lockout.list_locked(tenant)
    ]
    return LockoutOverview(settings=_settings_view(), locks=locks)


@router.delete(
    "/{ip}",
    response_model=LockoutOverview,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def unlock_ip(
    ip: str,
    request: Request,
    body: UnlockBody | None = None,
) -> LockoutOverview:
    tenant = current_tenant_id(request)
    actor = _actor_id(request)
    parsed = _validate_ip(ip)
    state = pat_auth_lockout.lock_state(parsed)
    # Tenant isolation: only let an admin unlock a lock that is
    # associated with their workspace (or has no associated tenant,
    # the "unknown PAT secret" case which any admin can clear since
    # it could be a probe against any of their PATs).
    if state.last_tenant_id and state.last_tenant_id != tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="lock not found for this workspace",
        )
    reason = (body.reason if body else "") or ""
    lifted = pat_auth_lockout.admin_unlock(
        parsed,
        tenant_id=tenant,
        by=actor,
        reason=reason,
    )
    write_event(
        {
            "actor": actor,
            "tenant_id": tenant,
            "action": "pat_auth_lockout.unlock",
            "target": parsed,
            "request_id": getattr(request.state, "request_id", ""),
            "before": {
                "locked": state.locked,
                "failures": state.failures,
                "locked_until": state.locked_until,
            },
            "after": {"locked": False, "reason": reason, "lift_took_effect": lifted},
        }
    )
    locks = [
        LockEntry(**st.to_dict()) for st in pat_auth_lockout.list_locked(tenant)
    ]
    return LockoutOverview(settings=_settings_view(), locks=locks)
