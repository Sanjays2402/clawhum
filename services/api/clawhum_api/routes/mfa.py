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
from ..api_keys import ANON_TENANT_ID
from ..auth import require_api_key

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
    stolen API key alone cannot turn the second factor off."""
    api_key = request.headers.get("x-api-key", "")
    actor_id = mfa.actor_id_for(api_key)
    result = mfa.disable(actor_id, body.code)
    if not result.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=result.get("error", "disable_failed"),
        )
    return {"ok": True}
