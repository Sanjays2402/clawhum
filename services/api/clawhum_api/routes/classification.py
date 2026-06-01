"""Per-workspace data classification administration.

Read is admin-only. Write requires admin with MFA, identical to the
residency, retention and incidents surfaces.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, constr

from .. import classification_store
from ..audit import write_event
from ..auth import require_admin_with_mfa, require_roles
from ..tenant import current_tenant

router = APIRouter(tags=["classification"], prefix="/classification")


class ClassificationOut(BaseModel):
    tenant_id: str
    level: str
    label: str
    handling_contact: str
    updated_at: float
    updated_by: str


class ClassificationReadResponse(BaseModel):
    classification: ClassificationOut
    available_levels: list[str]
    requires_ack: bool
    ack_header: str


class ClassificationUpdate(BaseModel):
    level: constr(min_length=1, max_length=16)  # type: ignore[valid-type]
    label: str = Field(default="", max_length=120)
    handling_contact: str = Field(default="", max_length=200)


def _to_out(c: classification_store.Classification) -> ClassificationOut:
    return ClassificationOut(
        tenant_id=c.tenant_id,
        level=c.level,
        label=c.label,
        handling_contact=c.handling_contact,
        updated_at=c.updated_at,
        updated_by=c.updated_by,
    )


@router.get(
    "",
    response_model=ClassificationReadResponse,
    dependencies=[Depends(require_roles("admin"))],
)
async def read_classification(
    tenant_id: str = Depends(current_tenant),
) -> ClassificationReadResponse:
    current = classification_store.get(tenant_id)
    return ClassificationReadResponse(
        classification=_to_out(current),
        available_levels=list(classification_store.VALID_LEVELS),
        requires_ack=classification_store.requires_ack(current.level),
        ack_header="X-Classification-Ack",
    )


@router.put(
    "",
    response_model=ClassificationOut,
    dependencies=[Depends(require_admin_with_mfa())],
)
async def update_classification(
    body: ClassificationUpdate,
    request: Request,
    tenant_id: str = Depends(current_tenant),
) -> ClassificationOut:
    level = (body.level or "").lower()
    if level not in classification_store._LEVEL_SET:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "unknown classification level; "
                f"allowed: {list(classification_store.VALID_LEVELS)}"
            ),
        )
    before = classification_store.get(tenant_id)
    actor = getattr(request.state, "api_key_name", "") or "admin"
    saved = classification_store.set_(
        tenant_id=tenant_id,
        level=level,
        label=body.label,
        handling_contact=body.handling_contact,
        actor=actor,
    )
    write_event(
        {
            "ts": saved.updated_at,
            "actor": actor,
            "tenant_id": tenant_id,
            "action": "classification.update",
            "target": tenant_id,
            "request_id": getattr(request.state, "request_id", ""),
            "before": before.to_dict(),
            "after": saved.to_dict(),
        }
    )
    return _to_out(saved)
