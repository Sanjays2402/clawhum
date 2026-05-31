"""MFA enrollment and management endpoints.

These power the ``/settings/security`` page in the web app and the
``require_mfa()`` dependency that gates destructive admin endpoints.
Read endpoints are intentionally cheap and side-effect free so the
client can poll for status without hammering the audit log.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import mfa
from .. import mfa_lockout
from .. import mfa_session
from .. import audit
from ..api_keys import ANON_TENANT_ID
from ..auth import require_api_key, require_roles

router = APIRouter(tags=["mfa"])


class StatusResponse(BaseModel):
    enrolled: bool
    verified: bool
    recovery_remaining: int
    created_at: float | None = None
    verified_at: float | None = None


class EnrollResponse(BaseModel):
    secret: str = Field(description="Base32 TOTP secret. Show once, never log.")
    otpauth: str = Field(description="otpauth:// URI for QR rendering or paste.")
    digits: int
    period: int


class VerifyBody(BaseModel):
    code: str = Field(min_length=4, max_length=12)


class VerifyResponse(BaseModel):
    ok: bool
    recovery_codes: list[str] = Field(
        default_factory=list,
        description="Single-use recovery codes. Shown once at verification.",
    )


class DisableBody(BaseModel):
    code: str = Field(min_length=4, max_length=12)


def _actor_label(request: Request, api_key: str) -> tuple[str, str]:
    actor_id = mfa.actor_id_for(api_key)
    tenant = getattr(request.state, "tenant_id", ANON_TENANT_ID) or ANON_TENANT_ID
    name = getattr(request.state, "api_key_name", "") or "key"
    tail = api_key[-4:] if api_key and len(api_key) > 4 else "dev"
    return actor_id, f"{tenant}/{name}/{tail}"


@router.get("/mfa/status", response_model=StatusResponse,
            dependencies=[Depends(require_api_key)])
async def mfa_status(request: Request) -> StatusResponse:
    api_key = request.headers.get("x-api-key", "")
    actor_id = mfa.actor_id_for(api_key)
    return StatusResponse(**mfa.status(actor_id))


@router.post("/mfa/enroll", response_model=EnrollResponse,
             dependencies=[Depends(require_api_key)])
async def mfa_enroll(request: Request) -> EnrollResponse:
    """Begin (or restart) MFA enrollment for the calling actor.

    Refuses if a verified record already exists; the user must disable
    first. This prevents an attacker who has stolen a live token from
    silently re-pairing a new authenticator without the legitimate user
    noticing they lost code parity.
    """
    api_key = request.headers.get("x-api-key", "")
    actor_id, label = _actor_label(request, api_key)
    result = mfa.begin_enrollment(actor_id, tenant_id=getattr(request.state, "tenant_id", ""),
                                  account_label=label)
    if result.get("already_verified"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="mfa already verified; disable first",
        )
    return EnrollResponse(**result)


@router.post("/mfa/verify", response_model=VerifyResponse,
             dependencies=[Depends(require_api_key)])
async def mfa_verify(body: VerifyBody, request: Request) -> VerifyResponse:
    api_key = request.headers.get("x-api-key", "")
    actor_id = mfa.actor_id_for(api_key)
    result = mfa.complete_enrollment(actor_id, body.code)
    if not result.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "verify_failed"),
        )
    return VerifyResponse(ok=True, recovery_codes=result.get("recovery_codes", []))


@router.delete("/mfa", dependencies=[Depends(require_api_key)])
async def mfa_disable(body: DisableBody, request: Request) -> dict[str, bool]:
    """Disable MFA. Requires a current TOTP or recovery code so a
    stolen API key alone cannot turn the second factor off.

    Disable is the same brute-force surface as ``require_mfa()`` so the
    same lockout policy applies: once the actor is locked, further
    disable attempts return HTTP 429 with Retry-After until the
    cooldown expires or a workspace admin clears the lock."""
    api_key = request.headers.get("x-api-key", "")
    actor_id = mfa.actor_id_for(api_key)
    tenant_id = getattr(request.state, "tenant_id", "") or ""
    pre = mfa_lockout.lock_state(actor_id)
    if pre.locked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="mfa locked: too many failed attempts",
            headers={"Retry-After": str(pre.retry_after)},
        )
    result = mfa.disable(actor_id, body.code)
    if not result.get("ok"):
        if result.get("error") == "invalid_code":
            after = mfa_lockout.record_failure(actor_id, tenant_id=tenant_id)
            audit.write_event(
                {
                    "event": "mfa.failed",
                    "actor_id": actor_id,
                    "tenant_id": tenant_id,
                    "path": request.url.path,
                    "failures": after.failures,
                    "locked": after.locked,
                }
            )
            if after.locked:
                audit.write_event(
                    {
                        "event": "mfa.locked",
                        "actor_id": actor_id,
                        "tenant_id": tenant_id,
                        "locked_until": after.locked_until,
                    }
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="mfa locked: too many failed attempts",
                    headers={"Retry-After": str(after.retry_after)},
                )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=result.get("error", "disable_failed"),
        )
    mfa_lockout.clear(actor_id, tenant_id=tenant_id, reason="disable")
    # Invalidate any outstanding step-up session tokens issued to this
    # actor so a previously minted sudo token cannot survive disabling
    # the second factor that authorised it.
    mfa_session.bump_epoch(tenant_id or ANON_TENANT_ID, actor_id)
    audit.write_event(
        {
            "event": "mfa.session.revoked",
            "actor_id": actor_id,
            "tenant_id": tenant_id,
            "reason": "mfa_disabled",
        }
    )
    return {"ok": True}


class StepUpSessionStatus(BaseModel):
    enabled: bool
    ttl_seconds: int
    max_ttl_seconds: int
    epoch: int


class StepUpIssueBody(BaseModel):
    code: str = Field(min_length=4, max_length=12)
    ttl_seconds: int | None = Field(
        default=None, ge=1,
        description="Optional client-requested TTL; clamped to the server setting.",
    )


class StepUpIssueResponse(BaseModel):
    token: str
    ttl_seconds: int
    expires_at: int


@router.get("/mfa/session", response_model=StepUpSessionStatus,
            dependencies=[Depends(require_api_key)])
async def mfa_session_status(request: Request) -> StepUpSessionStatus:
    api_key = request.headers.get("x-api-key", "")
    actor_id = mfa.actor_id_for(api_key)
    tenant_id = getattr(request.state, "tenant_id", "") or ANON_TENANT_ID
    return StepUpSessionStatus(**mfa_session.status(tenant_id, actor_id))


@router.post("/mfa/session", response_model=StepUpIssueResponse,
             dependencies=[Depends(require_api_key)])
async def mfa_session_issue(body: StepUpIssueBody, request: Request) -> StepUpIssueResponse:
    """Exchange a fresh TOTP/recovery code for a short-lived step-up
    session token. The returned ``X-MFA-Session`` token may be presented
    in place of ``X-MFA-Code`` until it expires.

    The exchange uses the same brute-force surface and audit hooks as
    every other MFA verification.
    """
    api_key = request.headers.get("x-api-key", "")
    actor_id = mfa.actor_id_for(api_key)
    tenant_id = getattr(request.state, "tenant_id", "") or ANON_TENANT_ID
    if not mfa.is_required(actor_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="mfa not enrolled",
        )
    if mfa_session.effective_ttl_seconds(tenant_id) <= 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="mfa step-up session disabled for workspace",
        )
    pre = mfa_lockout.lock_state(actor_id)
    if pre.locked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="mfa locked: too many failed attempts",
            headers={"Retry-After": str(pre.retry_after)},
        )
    if not mfa.verify(actor_id, body.code):
        after = mfa_lockout.record_failure(actor_id, tenant_id=tenant_id)
        audit.write_event(
            {
                "event": "mfa.failed",
                "actor_id": actor_id,
                "tenant_id": tenant_id,
                "path": request.url.path,
                "failures": after.failures,
                "locked": after.locked,
            }
        )
        if after.locked:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="mfa locked: too many failed attempts",
                headers={"Retry-After": str(after.retry_after)},
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid mfa code",
        )
    mfa_lockout.clear(actor_id, tenant_id=tenant_id, reason="session_issue")
    issued = mfa_session.issue(
        tenant_id=tenant_id, actor_id=actor_id,
        ttl_seconds=body.ttl_seconds,
    )
    audit.write_event(
        {
            "event": "mfa.session.issued",
            "actor_id": actor_id,
            "tenant_id": tenant_id,
            "ttl_seconds": issued.ttl_seconds,
            "expires_at": issued.expires_at,
        }
    )
    return StepUpIssueResponse(
        token=issued.token,
        ttl_seconds=issued.ttl_seconds,
        expires_at=issued.expires_at,
    )


@router.delete("/mfa/session",
               dependencies=[Depends(require_api_key)])
async def mfa_session_revoke(request: Request) -> dict[str, bool]:
    """Revoke every step-up session token outstanding for the calling
    actor. Used by the dashboard's \"lock sudo mode\" button and by
    the workspace-wide force-logout flow."""
    api_key = request.headers.get("x-api-key", "")
    actor_id = mfa.actor_id_for(api_key)
    tenant_id = getattr(request.state, "tenant_id", "") or ANON_TENANT_ID
    epoch = mfa_session.revoke_all(tenant_id, actor_id)
    audit.write_event(
        {
            "event": "mfa.session.revoked",
            "actor_id": actor_id,
            "tenant_id": tenant_id,
            "reason": "user_revoke",
            "epoch": epoch,
        }
    )
    return {"ok": True}


class LockoutEntry(BaseModel):
    actor_id: str
    failures: int
    locked: bool
    locked_until: float
    retry_after: int


class LockoutListResponse(BaseModel):
    threshold: int
    window_seconds: int
    cooldown_seconds: int
    items: list[LockoutEntry]


class LockoutSelfResponse(BaseModel):
    actor_id: str
    failures: int
    locked: bool
    retry_after: int


@router.get("/mfa/lockout", response_model=LockoutSelfResponse,
            dependencies=[Depends(require_api_key)])
async def mfa_lockout_self(request: Request) -> LockoutSelfResponse:
    """Return the calling actor's own lockout state.

    Lets the security settings page render an actionable banner when the
    user (or someone holding their key) has tripped the cooldown, so the
    user knows to wait or to ask an admin to unlock.
    """
    api_key = request.headers.get("x-api-key", "")
    actor_id = mfa.actor_id_for(api_key)
    st = mfa_lockout.lock_state(actor_id)
    return LockoutSelfResponse(
        actor_id=actor_id,
        failures=st.failures,
        locked=st.locked,
        retry_after=st.retry_after,
    )


@router.get("/admin/mfa/lockouts", response_model=LockoutListResponse,
            dependencies=[Depends(require_roles("admin"))])
async def admin_lockouts(request: Request) -> LockoutListResponse:
    """List currently-locked actors in the caller's workspace.

    Read-only; intended for the admin console strip. We never expose
    the raw API key here, only the hashed actor identifier the audit
    log already uses so operators can correlate without handling
    secrets.
    """
    from clawhum_core.settings import get_settings
    s = get_settings()
    tenant_id = getattr(request.state, "tenant_id", "") or ANON_TENANT_ID
    items = [
        LockoutEntry(
            actor_id=st.actor_id,
            failures=st.failures,
            locked=st.locked,
            locked_until=st.locked_until,
            retry_after=st.retry_after,
        )
        for st in mfa_lockout.list_locked(tenant_id)
    ]
    return LockoutListResponse(
        threshold=int(s.mfa_lockout_threshold),
        window_seconds=int(s.mfa_lockout_window_seconds),
        cooldown_seconds=int(s.mfa_lockout_cooldown_seconds),
        items=items,
    )


class AdminUnlockBody(BaseModel):
    actor_id: str = Field(min_length=4, max_length=128)
    reason: str = Field(default="", max_length=256)


@router.post("/admin/mfa/lockouts/unlock",
             dependencies=[Depends(require_roles("admin"))])
async def admin_unlock_lockout(body: AdminUnlockBody, request: Request) -> dict[str, bool]:
    """Manually clear a locked actor's cooldown.

    Records the unlock in both the lockout log and the tamper-evident
    audit chain so an after-action review can attribute the override
    to a specific admin actor.
    """
    tenant_id = getattr(request.state, "tenant_id", "") or ANON_TENANT_ID
    api_key = request.headers.get("x-api-key", "")
    by_actor = mfa.actor_id_for(api_key)
    lifted = mfa_lockout.admin_unlock(
        body.actor_id, tenant_id=tenant_id, by=by_actor, reason=body.reason
    )
    audit.write_event(
        {
            "event": "mfa.unlocked",
            "actor_id": body.actor_id,
            "tenant_id": tenant_id,
            "by": by_actor,
            "reason": body.reason,
            "was_locked": lifted,
        }
    )
    return {"ok": True, "was_locked": lifted}
