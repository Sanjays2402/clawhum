from __future__ import annotations

from clawhum_core.settings import get_settings
from clawhum_library.feedback import record_feedback
from fastapi import APIRouter, Depends, Request

from ..auth import require_api_key
from ..schemas import FeedbackBody
from ..tenant import current_tenant_id

router = APIRouter(tags=["feedback"], dependencies=[Depends(require_api_key)])


@router.post("/feedback")
async def feedback(body: FeedbackBody, request: Request):
    s = get_settings()
    tenant_id = current_tenant_id(request)
    record_feedback(
        s.feedback_path,
        body.query_id,
        body.track_id,
        body.score,
        body.vote,
        tenant_id=tenant_id,
    )
    return {"ok": True, "tenant_id": tenant_id}
