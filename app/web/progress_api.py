"""内部进度回调：DSH 工具事件 → 当前回复的进度消息。"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..gateway.progress import get_progress

router = APIRouter(prefix="/api/internal", tags=["internal"])


class ProgressIn(BaseModel):
    token: str = ""
    turn_id: str = ""
    type: str = ""
    tool: str = ""
    text: str = ""


@router.post("/progress")
async def progress(req: ProgressIn):
    turn = get_progress(req.token)
    if turn is None:
        return {"ok": False, "error": "turn 不存在或已结束"}
    if req.turn_id and req.turn_id != turn.turn_id:
        return {"ok": False, "error": "turn_id 不匹配"}
    if req.type == "assistant/commentary":
        sent = await turn.emit_commentary(req.text)
    else:
        sent = await turn.emit_tool(req.type, req.tool)
    return {"ok": True, "sent": sent}
