from __future__ import annotations
from fastapi import APIRouter, Depends
from clawhum_core.settings import get_settings
from clawhum_library.feedback import record_feedback
from ..auth import require_api_key
from ..schemas import FeedbackBody

router = APIRouter(tags=["feedback"], dependencies=[Depends(require_api_key)])


@router.post("/feedback")
async def feedback(body: FeedbackBody):
    s = get_settings()
    record_feedback(s.feedback_path, body.query_id, body.track_id, body.score, body.vote)
    return {"ok": True}
