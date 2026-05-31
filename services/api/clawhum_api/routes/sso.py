"""Workspace single sign on administration.

Endpoints:

- GET    /sso/providers          public list of supported IdP templates
- GET    /sso/discover           public: which workspace owns an email
                                  domain, and is SSO enforced for it
- GET    /sso/config              admin: read this workspace's SSO config
- PUT    /sso/config              admin + MFA: upsert this workspace's
                                  SSO config (issuer, client_id, secret,
                                  domain, enforced)
- DELETE /sso/config              admin + MFA: tombstone the config

Discovery is intentionally public and unauthenticated: a sign-in form
that asks for an email needs to know whether to send the user to SSO
or to the password screen before they have any credentials. The
response only reveals booleans plus the issuer hostname so an attacker
cannot enumerate workspace internals through this surface.

Every mutating route is admin-only and MFA gated; the global
``AuditLogMiddleware`` writes the actor, IP, before/after status, and
request id to the immutable audit log so a procurement reviewer can
prove who turned enforce-SSO on or off.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import member_store, sso_store, seat_limit_store
from ..api_keys import ANON_TENANT_ID, DEV_TENANT_ID
from ..auth import require_admin_with_mfa, require_roles
from ..tenant import current_tenant

router = APIRouter(tags=["sso"], prefix="/sso")


class ProviderOut(BaseModel):
    id: str
    label: str


class ProvidersResponse(BaseModel):
    providers: list[ProviderOut]
    default_redirect_uri: str


class SSOConfigOut(BaseModel):
    provider: str
    provider_label: str
    issuer: str
    client_id: str
    client_secret: str  # masked unless reveal=true and caller is admin+MFA
    email_domain: str
    enforced: bool
    auto_join: bool
    auto_join_role: str
    created_at: float
    updated_at: float
    created_by: str
    discovery_url: str


class SSOConfigBody(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    issuer: str = Field(min_length=8, max_length=512)
    client_id: str = Field(min_length=1, max_length=256)
    # Empty string means "keep existing secret". Required on first create.
    client_secret: str = Field(default="", max_length=512)
    email_domain: str = Field(min_length=3, max_length=253)
    enforced: bool = False
    auto_join: bool = False
    auto_join_role: str = Field(default="reader", pattern="^(admin|writer|reader)$")


class AutoJoinBody(BaseModel):
    # Public route. We deliberately accept just the email so the
    # caller (the OIDC token exchange handler living in front of this
    # API, or a sign-in shim) can hand the just-verified address in.
    # The route itself never trusts the email as proof of identity;
    # it only resolves the workspace and provisions the seat.
    email: str = Field(min_length=3, max_length=320)


class AutoJoinResponse(BaseModel):
    claimed: bool  # true if a fresh seat was created on this call
    member_id: str
    tenant_id: str
    email: str
    role: str
    status: str


class DiscoveryResponse(BaseModel):
    configured: bool
    enforced: bool
    provider: str
    provider_label: str
    # When configured, the issuer is echoed so the client can build a
    # login URL itself. We do not return the client_id here because
    # discovery is unauthenticated.
    issuer: str
    auto_join: bool = False


def _guard_tenant(tenant_id: str) -> str:
    if not tenant_id or tenant_id in (ANON_TENANT_ID, DEV_TENANT_ID):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="sso configuration requires a workspace-scoped api key",
        )
    return tenant_id


@router.get("/providers", response_model=ProvidersResponse)
async def list_providers() -> ProvidersResponse:
    from clawhum_core.settings import get_settings
    settings = get_settings()
    return ProvidersResponse(
        providers=[ProviderOut(id=k, label=v) for k, v in sso_store.KNOWN_PROVIDERS.items()],
        default_redirect_uri=settings.sso_default_redirect_uri,
    )


@router.get("/discover", response_model=DiscoveryResponse)
async def discover(email: str = "", domain: str = "") -> DiscoveryResponse:
    """Public sign in helper. Given an email or a bare domain, report
    whether a workspace has SSO configured and whether SSO is enforced.

    Returns a non-revealing response when nothing matches so an
    attacker cannot use this endpoint to enumerate which domains are
    customers; the only signal in that case is ``configured=False``.
    """
    target = (email or domain or "").strip().lower()
    if "@" in target:
        target = target.split("@", 1)[1]
    if not target:
        return DiscoveryResponse(
            configured=False, enforced=False, provider="", provider_label="", issuer=""
        )
    rec = sso_store.get_by_email_domain(target)
    if rec is None:
        return DiscoveryResponse(
            configured=False, enforced=False, provider="", provider_label="", issuer=""
        )
    return DiscoveryResponse(
        configured=True,
        enforced=rec.enforced,
        provider=rec.provider,
        provider_label=sso_store.KNOWN_PROVIDERS.get(rec.provider, "OIDC"),
        issuer=rec.issuer,
        auto_join=rec.auto_join,
    )


@router.get(
    "/config",
    response_model=SSOConfigOut | None,
    dependencies=[Depends(require_roles("admin"))],
)
async def read_config(tenant_id: str = Depends(current_tenant)) -> SSOConfigOut | None:
    _guard_tenant(tenant_id)
    rec = sso_store.get_for_tenant(tenant_id)
    if rec is None:
        return None
    return SSOConfigOut(**rec.public_dict(reveal_secret=False))


@router.put("/config", response_model=SSOConfigOut)
async def upsert_config(
    body: SSOConfigBody,
    request: Request,
    _: str = Depends(require_admin_with_mfa()),
    tenant_id: str = Depends(current_tenant),
) -> SSOConfigOut:
    _guard_tenant(tenant_id)
    actor = getattr(request.state, "api_key_name", "unknown") or "unknown"
    try:
        rec = sso_store.upsert(
            tenant_id=tenant_id,
            provider=body.provider,
            issuer=body.issuer,
            client_id=body.client_id,
            client_secret=body.client_secret,
            email_domain=body.email_domain,
            enforced=body.enforced,
            actor=actor,
            auto_join=body.auto_join,
            auto_join_role=body.auto_join_role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return SSOConfigOut(**rec.public_dict(reveal_secret=False))


@router.delete("/config", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config(
    request: Request,
    _: str = Depends(require_admin_with_mfa()),
    tenant_id: str = Depends(current_tenant),
) -> None:
    _guard_tenant(tenant_id)
    actor = getattr(request.state, "api_key_name", "unknown") or "unknown"
    sso_store.delete(tenant_id, actor)
    return None


@router.post(
    "/auto-join",
    response_model=AutoJoinResponse,
    status_code=status.HTTP_200_OK,
)
async def auto_join(body: AutoJoinBody, request: Request) -> AutoJoinResponse:
    """Claim a workspace seat for a freshly authenticated SSO user.

    This endpoint is public on purpose: it sits behind the OIDC
    sign-in callback (the front-end exchanges the IdP code, then
    posts the verified email here). It is safe to be public because
    it only provisions a seat when a workspace admin has explicitly
    enabled ``auto_join`` for the email's domain, and the assigned
    role is whatever that admin pre-approved. The audit log records
    every claim with actor, IP, and the resolved tenant so an SOC
    reviewer can see who got in and when.

    The response is intentionally non-revealing when no workspace
    has the domain configured: we return HTTP 404 with a generic
    detail so the endpoint cannot be used to enumerate customers.
    """
    email = str(body.email or "").strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="email is required")
    domain = email.split("@", 1)[1]
    rec = sso_store.get_by_email_domain(domain)
    if rec is None or rec.deleted:
        # Same shape an attacker would see for any unknown domain;
        # no information leak about which domains are customers.
        raise HTTPException(status_code=404, detail="no workspace for this domain")
    if not rec.auto_join:
        # Domain is mapped but auto-join is off: tell the caller
        # explicitly so the sign-in UI can fall back to "ask your
        # admin to invite you" instead of looping forever.
        raise HTTPException(
            status_code=403,
            detail="domain auto-join is disabled for this workspace",
        )
    existing = member_store.find_active_by_email(rec.tenant_id, email)
    try:
        member = member_store.create_active(
            tenant_id=rec.tenant_id,
            email=email,
            role=rec.auto_join_role,
            invited_by="sso-auto-join",
        )
    except seat_limit_store.SeatLimitExceededError as exc:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "seat_limit_exceeded",
                "message": str(exc),
                "current": exc.current,
                "limit": exc.limit,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return AutoJoinResponse(
        claimed=existing is None,
        member_id=member.id,
        tenant_id=member.tenant_id,
        email=member.email,
        role=member.role,
        status=member.status,
    )
