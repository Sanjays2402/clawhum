"""SCIM 2.0 user provisioning endpoints (RFC 7643 / RFC 7644).

Why this exists: enterprise procurement teams require SCIM so their
identity provider (Okta, Azure AD, Google Workspace) can push joiners
and leavers automatically. Without SCIM, every termination becomes a
manual ticket and ex-employees keep workspace access for days.

Surface area mounted under /scim/v2 and /v1/scim/v2:

- GET    /ServiceProviderConfig         capability advertisement
- GET    /Schemas                       supported attribute schemas
- GET    /ResourceTypes                 supported resource types
- GET    /Users                         list + filter (eq on userName only)
- POST   /Users                         provision a new user
- GET    /Users/{id}                    fetch one user
- PUT    /Users/{id}                    replace (role + active)
- PATCH  /Users/{id}                    de-provision via active=false
- DELETE /Users/{id}                    hard tombstone the seat

Authentication: a per-tenant bearer token minted by an admin via the
existing admin console. The token resolves to a tenant_id; every list
and mutation is scoped to that tenant by member_store so no SCIM
caller can ever see or touch another workspace's roster.

Mutations land through the same member_store used by the human admin
console, so audit log, RBAC, and the /members view stay the single
source of truth. The middleware stack already writes audit entries
for every mutating request, so we do not duplicate that here.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from clawhum_core.settings import get_settings

from .. import member_store, scim_tokens
from ..api_keys import ROLES
from ..member_store import STATUS_ACTIVE, STATUS_REVOKED


router = APIRouter(tags=["scim"], prefix="/scim/v2")

SCIM_CONTENT_TYPE = "application/scim+json"
SCHEMA_USER = "urn:ietf:params:scim:schemas:core:2.0:User"
SCHEMA_LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCHEMA_ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
SCHEMA_PATCHOP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCHEMA_SPC = "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
SCHEMA_RESOURCE_TYPE = "urn:ietf:params:scim:schemas:core:2.0:ResourceType"
SCHEMA_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Schema"

# Custom role attribute lives in an extension namespace so it does not
# collide with the core User schema. IdPs that do not know about it can
# leave the field unset and members default to the reader role.
SCHEMA_ENTERPRISE_EXT = "urn:clawhum:scim:schemas:extension:2.0:User"
DEFAULT_ROLE = "reader"


def _scim_error(detail: str, code: int) -> HTTPException:
    """Build an HTTPException carrying a SCIM 2.0 Error response body.

    FastAPI's default error shape is {"detail": "..."}; SCIM clients
    expect {"schemas": [...], "detail": "...", "status": "..."}. We
    raise an HTTPException with the SCIM body so the FastAPI exception
    handler returns the correct payload.
    """
    return HTTPException(
        status_code=code,
        detail={
            "schemas": [SCHEMA_ERROR],
            "detail": detail,
            "status": str(code),
        },
    )


def _require_scim_bearer(request: Request, authorization: str) -> str:
    """Validate the SCIM bearer token. Returns the resolved tenant_id.

    Returns 401 for missing, malformed, unknown, or revoked tokens.
    Stamps request.state so the audit middleware records the correct
    actor and tenant for the SCIM caller, and bumps last_used.
    """
    settings = get_settings()
    if not settings.scim_enabled:
        raise _scim_error("scim is disabled on this deployment", status.HTTP_404_NOT_FOUND)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _scim_error("missing bearer token", status.HTTP_401_UNAUTHORIZED)
    token = authorization.split(" ", 1)[1].strip()
    row = scim_tokens.lookup(token)
    if row is None:
        raise _scim_error("invalid scim bearer token", status.HTTP_401_UNAUTHORIZED)
    # Stamp request state so AuditLogMiddleware credits the correct
    # tenant and the SCIM actor name shows up in the audit feed.
    request.state.api_key_name = f"scim:{row.tenant_id}"
    request.state.api_key_roles = frozenset({"admin"})
    request.state.tenant_id = row.tenant_id
    request.state.scim_token = True
    try:
        scim_tokens.touch_last_used(row.tenant_id)
    except Exception:
        pass
    return row.tenant_id


def _user_resource(m: member_store.Member, base_url: str) -> dict[str, Any]:
    """Serialize a Member as a SCIM 2.0 User resource."""
    return {
        "schemas": [SCHEMA_USER, SCHEMA_ENTERPRISE_EXT],
        "id": m.id,
        "userName": m.email,
        "active": m.status == STATUS_ACTIVE,
        "emails": [{"value": m.email, "primary": True, "type": "work"}],
        "meta": {
            "resourceType": "User",
            "created": _iso(m.invited_at),
            "lastModified": _iso(m.accepted_at or m.invited_at),
            "location": f"{base_url}/Users/{m.id}",
        },
        SCHEMA_ENTERPRISE_EXT: {"role": m.role},
    }


def _iso(ts: float) -> str:
    if ts <= 0:
        return "1970-01-01T00:00:00Z"
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _extract_role(payload: dict[str, Any]) -> str:
    """Pull the workspace role out of the optional enterprise extension."""
    ext = payload.get(SCHEMA_ENTERPRISE_EXT) or {}
    role = (ext.get("role") or DEFAULT_ROLE).strip().lower()
    if role not in ROLES:
        raise _scim_error(f"invalid role: {role}", status.HTTP_400_BAD_REQUEST)
    return role


def _base_url(request: Request) -> str:
    # Build the canonical Users location header. We always use the
    # request's own scheme + host so links stay valid behind a proxy.
    return str(request.url).split("/scim/v2", 1)[0] + "/scim/v2"


# --- Discovery endpoints ---------------------------------------------------

@router.get("/ServiceProviderConfig")
def service_provider_config(request: Request) -> Response:
    body = {
        "schemas": [SCHEMA_SPC],
        "documentationUri": "https://datatracker.ietf.org/doc/html/rfc7644",
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 200},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {
                "type": "oauthbearertoken",
                "name": "OAuth Bearer Token",
                "description": "Per-workspace static bearer token minted by an admin",
                "primary": True,
            }
        ],
    }
    return Response(content=_json(body), media_type=SCIM_CONTENT_TYPE)


@router.get("/ResourceTypes")
def resource_types(request: Request) -> Response:
    base = _base_url(request)
    body = {
        "schemas": [SCHEMA_LIST],
        "totalResults": 1,
        "Resources": [
            {
                "schemas": [SCHEMA_RESOURCE_TYPE],
                "id": "User",
                "name": "User",
                "endpoint": "/Users",
                "schema": SCHEMA_USER,
                "schemaExtensions": [
                    {"schema": SCHEMA_ENTERPRISE_EXT, "required": False}
                ],
                "meta": {
                    "resourceType": "ResourceType",
                    "location": f"{base}/ResourceTypes/User",
                },
            }
        ],
    }
    return Response(content=_json(body), media_type=SCIM_CONTENT_TYPE)


@router.get("/Schemas")
def schemas() -> Response:
    body = {
        "schemas": [SCHEMA_LIST],
        "totalResults": 2,
        "Resources": [
            {"id": SCHEMA_USER, "name": "User"},
            {
                "id": SCHEMA_ENTERPRISE_EXT,
                "name": "ClawHumWorkspaceUser",
                "description": "Workspace role attribute for ClawHum members",
            },
        ],
    }
    return Response(content=_json(body), media_type=SCIM_CONTENT_TYPE)


# --- Users -----------------------------------------------------------------

@router.get("/Users")
def list_users(
    request: Request,
    filter: str | None = None,  # noqa: A002 - SCIM spec uses "filter"
    startIndex: int = 1,
    count: int = 100,
    authorization: str = Header(default=""),
) -> Response:
    tenant_id = _require_scim_bearer(request, authorization)
    members = member_store.list_for_tenant(tenant_id)

    # We only support the userName eq "..." filter that Okta, Azure AD,
    # and Google Workspace all rely on for the existence check before
    # POST. Anything else returns an empty result set rather than a 400
    # so non-matching IdP probes degrade gracefully.
    if filter:
        members = [m for m in members if _matches_username_filter(filter, m.email)]

    start = max(1, startIndex)
    page_count = max(0, min(count, 200))
    sliced = members[start - 1 : start - 1 + page_count]
    base = _base_url(request)
    body = {
        "schemas": [SCHEMA_LIST],
        "totalResults": len(members),
        "startIndex": start,
        "itemsPerPage": len(sliced),
        "Resources": [_user_resource(m, base) for m in sliced],
    }
    return Response(content=_json(body), media_type=SCIM_CONTENT_TYPE)


class _UserCreate(BaseModel):
    schemas: list[str] = Field(default_factory=list)
    userName: str = Field(min_length=3, max_length=320)
    active: bool = True
    model_config = {"extra": "allow"}


@router.post("/Users", status_code=status.HTTP_201_CREATED)
def create_user(
    request: Request,
    body: dict[str, Any],
    authorization: str = Header(default=""),
) -> Response:
    tenant_id = _require_scim_bearer(request, authorization)
    try:
        parsed = _UserCreate.model_validate(body)
    except Exception as e:  # pydantic ValidationError surface
        raise _scim_error(f"invalid user payload: {e}", status.HTTP_400_BAD_REQUEST)

    role = _extract_role(body)
    email = parsed.userName.strip().lower()
    if not member_store.is_valid_email(email):
        raise _scim_error("invalid userName", status.HTTP_400_BAD_REQUEST)

    existing = member_store.find_active_by_email(tenant_id, email)
    if existing is not None:
        # SCIM expects 409 on duplicate userName so the IdP can switch to PUT.
        raise _scim_error("user already exists", status.HTTP_409_CONFLICT)

    member = member_store.create_active(
        tenant_id=tenant_id,
        email=email,
        role=role,
        invited_by=f"scim:{tenant_id}",
    )
    if not parsed.active:
        # Provisioned in a disabled state. Revoke immediately so RBAC
        # cannot grant any access, and we keep idempotency with IdPs
        # that always POST active=false first then PATCH later.
        member = member_store.revoke(member.id, tenant_id=tenant_id)
    base = _base_url(request)
    resource = _user_resource(member, base)
    return Response(
        content=_json(resource),
        media_type=SCIM_CONTENT_TYPE,
        status_code=status.HTTP_201_CREATED,
        headers={"Location": resource["meta"]["location"]},
    )


@router.get("/Users/{user_id}")
def get_user(
    user_id: str,
    request: Request,
    authorization: str = Header(default=""),
) -> Response:
    tenant_id = _require_scim_bearer(request, authorization)
    m = member_store.get(user_id)
    if m is None or m.tenant_id != tenant_id:
        # Same shape for "wrong tenant" and "not found" so SCIM callers
        # cannot enumerate member ids across workspaces.
        raise _scim_error("user not found", status.HTTP_404_NOT_FOUND)
    return Response(content=_json(_user_resource(m, _base_url(request))), media_type=SCIM_CONTENT_TYPE)


@router.put("/Users/{user_id}")
def replace_user(
    user_id: str,
    request: Request,
    body: dict[str, Any],
    authorization: str = Header(default=""),
) -> Response:
    tenant_id = _require_scim_bearer(request, authorization)
    m = member_store.get(user_id)
    if m is None or m.tenant_id != tenant_id:
        raise _scim_error("user not found", status.HTTP_404_NOT_FOUND)

    role = _extract_role(body)
    active = bool(body.get("active", True))
    # Update role first so a follow-up revoke still records the new role
    # in the audit log, then toggle active state via revoke when needed.
    if role != m.role and m.status != STATUS_REVOKED:
        m = member_store.update_role(user_id, role=role, tenant_id=tenant_id)
    if not active and m.status != STATUS_REVOKED:
        m = member_store.revoke(user_id, tenant_id=tenant_id)
    if active and m.status == STATUS_REVOKED:
        # Re-provision: SCIM PUT with active=true on a tombstoned user
        # should bring them back with their last known role + email.
        m = member_store.create_active(
            tenant_id=tenant_id,
            email=m.email,
            role=role,
            invited_by=f"scim:{tenant_id}",
        )
    return Response(content=_json(_user_resource(m, _base_url(request))), media_type=SCIM_CONTENT_TYPE)


@router.patch("/Users/{user_id}")
def patch_user(
    user_id: str,
    request: Request,
    body: dict[str, Any],
    authorization: str = Header(default=""),
) -> Response:
    """Handle the narrow PatchOp set that real IdPs send.

    Okta and Azure AD both use PATCH with a single operation of the
    form ``{"op": "Replace", "path": "active", "value": false}`` for
    de-provisioning, and ``{"op": "Replace", "value": {...}}`` for
    attribute updates. We honour those plus the role extension; richer
    SCIM filter paths return 400 so misuse is loud rather than silent.
    """
    tenant_id = _require_scim_bearer(request, authorization)
    m = member_store.get(user_id)
    if m is None or m.tenant_id != tenant_id:
        raise _scim_error("user not found", status.HTTP_404_NOT_FOUND)
    ops = body.get("Operations") or []
    if not isinstance(ops, list) or not ops:
        raise _scim_error("Operations is required", status.HTTP_400_BAD_REQUEST)

    new_active: bool | None = None
    new_role: str | None = None
    for op in ops:
        if not isinstance(op, dict):
            continue
        action = (op.get("op") or "").lower()
        if action not in {"replace", "add"}:
            # We do not support remove for the attributes we expose;
            # de-provisioning flows through active=false instead.
            raise _scim_error(f"unsupported op: {action}", status.HTTP_400_BAD_REQUEST)
        path = (op.get("path") or "").strip()
        value = op.get("value")
        if path == "active":
            new_active = bool(value)
        elif path == f"{SCHEMA_ENTERPRISE_EXT}:role":
            role = (str(value or "")).strip().lower()
            if role not in ROLES:
                raise _scim_error(f"invalid role: {role}", status.HTTP_400_BAD_REQUEST)
            new_role = role
        elif not path and isinstance(value, dict):
            if "active" in value:
                new_active = bool(value["active"])
            ext = value.get(SCHEMA_ENTERPRISE_EXT) or {}
            if "role" in ext:
                role = (str(ext["role"] or "")).strip().lower()
                if role not in ROLES:
                    raise _scim_error(f"invalid role: {role}", status.HTTP_400_BAD_REQUEST)
                new_role = role
        else:
            raise _scim_error(f"unsupported path: {path}", status.HTTP_400_BAD_REQUEST)

    if new_role and m.status != STATUS_REVOKED and new_role != m.role:
        m = member_store.update_role(user_id, role=new_role, tenant_id=tenant_id)
    if new_active is False and m.status != STATUS_REVOKED:
        m = member_store.revoke(user_id, tenant_id=tenant_id)
    elif new_active is True and m.status == STATUS_REVOKED:
        m = member_store.create_active(
            tenant_id=tenant_id,
            email=m.email,
            role=new_role or m.role,
            invited_by=f"scim:{tenant_id}",
        )
    return Response(content=_json(_user_resource(m, _base_url(request))), media_type=SCIM_CONTENT_TYPE)


@router.delete("/Users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: str,
    request: Request,
    authorization: str = Header(default=""),
) -> Response:
    tenant_id = _require_scim_bearer(request, authorization)
    m = member_store.get(user_id)
    if m is None or m.tenant_id != tenant_id:
        raise _scim_error("user not found", status.HTTP_404_NOT_FOUND)
    member_store.revoke(user_id, tenant_id=tenant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- helpers ---------------------------------------------------------------

def _matches_username_filter(expr: str, email: str) -> bool:
    """Best effort parse of ``userName eq "value"`` style SCIM filters.

    Real-world IdPs only ever send this single form during the
    existence check before POST. Anything fancier is treated as a
    no-match rather than a 400 so probing does not break syncs.
    """
    s = expr.strip()
    low = s.lower()
    if not low.startswith("username eq "):
        return False
    rhs = s[len("username eq "):].strip()
    if rhs.startswith('"') and rhs.endswith('"'):
        rhs = rhs[1:-1]
    return rhs.strip().lower() == email.lower()


def _json(body: dict[str, Any]) -> str:
    import json as _json_mod
    return _json_mod.dumps(body, separators=(",", ":"), sort_keys=False)
