"""Per-workspace PAT secret prefix policy administration.

Admin-only set/clear of a custom prefix that newly minted PAT
secrets must carry. Read is open to readers so the dashboard can
show the current pin alongside a copy-pasteable secret-scanner
regex. Tenant scoped on every call.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import pat_secret_prefix
from ..audit import write_event
from ..auth import require_admin_with_mfa, require_roles
from ..tenant import current_tenant_id

router = APIRouter(tags=["pat-secret-prefix"], prefix="/pat-secret-prefix")


class PrefixResponse(BaseModel):
    enforcing: bool
    prefix: str
    example_secret: str
    scanner_regex: str
    updated_at: float = 0.0
    updated_by: str = ""


class PrefixUpdate(BaseModel):
    prefix: str = Field(
        default="",
        description=(
            "Lower-case [a-z0-9-], 2-16 chars, no leading or trailing "
            "dash, no underscore. Pass an empty string to clear the "
            "policy and return to the global pat_ shape."
        ),
        max_length=32,
    )


def _actor_id(request: Request) -> str:
    return (
        getattr(request.state, "api_key_name", "")
        or getattr(request.state, "pat_id", "")
        or "unknown"
    )


def _to_response(request: Request) -> PrefixResponse:
    tenant = current_tenant_id(request)
    pol = pat_secret_prefix.get_policy(tenant)
    prefix = pol.prefix if pol else ""
    example = (
        f"pat_{prefix}_AbCdEfGhIjKlMnOpQrStUvWx"
        if prefix
        else "pat_AbCdEfGhIjKlMnOpQrStUvWx"
    )
    return PrefixResponse(
        enforcing=bool(prefix),
        prefix=prefix,
        example_secret=example,
        scanner_regex=pat_secret_prefix.scanner_regex(prefix),
        updated_at=pol.updated_at if pol else 0.0,
        updated_by=pol.updated_by if pol else "",
    )


@router.get(
    "",
    response_model=PrefixResponse,
    dependencies=[Depends(require_roles("reader"))],
)
async def get_pat_secret_prefix(request: Request) -> PrefixResponse:
    return _to_response(request)


@router.put(
    "",
    response_model=PrefixResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def set_pat_secret_prefix(
    body: PrefixUpdate, request: Request
) -> PrefixResponse:
    tenant = current_tenant_id(request)
    actor = _actor_id(request)
    before = pat_secret_prefix.get_policy(tenant)
    try:
        saved = pat_secret_prefix.set_policy(
            tenant_id=tenant,
            prefix=body.prefix,
            updated_by=actor,
        )
    except pat_secret_prefix.InvalidPrefixError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_pat_prefix",
                "message": str(exc),
            },
        )
    write_event(
        {
            "ts": saved.updated_at,
            "actor": actor,
            "tenant_id": tenant,
            "action": "pat_secret_prefix.update",
            "target": tenant,
            "request_id": getattr(request.state, "request_id", ""),
            "before": before.to_dict() if before else {"prefix": ""},
            "after": saved.to_dict(),
        }
    )
    return _to_response(request)
