"""主动私聊接口。

- status：开关、今天发了多少、候选和分数
- preview：按当前时间算一遍（会生成内容，但不发）
- fire：手动叫她说一句（测试用）
- logs：最近的主动消息
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/proactive", tags=["proactive"])


class PreviewIn(BaseModel):
    account_id: str = ""
    now: float | None = None


class FireIn(BaseModel):
    account_id: str
    qq: str
    text: str = ""
    now: float | None = None


class TickIn(BaseModel):
    account_id: str = ""
    dry_run: bool = True
    now: float | None = None


def _svc(request: Request):
    svc = getattr(request.app.state, "proactive", None)
    if svc is None:
        raise RuntimeError("主动私聊服务没起来")
    return svc


@router.get("/status")
def status(request: Request, account_id: str = ""):
    try:
        return _svc(request).status(account_id=account_id)
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}


@router.post("/preview")
async def preview(req: PreviewIn, request: Request):
    try:
        return {"ok": True, "decisions": await _svc(request).preview(
            account_id=req.account_id, now=req.now
        )}
    except Exception as e:  # noqa: BLE001
        log.exception("主动私聊预览失败")
        return {"ok": False, "error": str(e)[:300]}


@router.post("/fire")
async def fire(req: FireIn, request: Request):
    try:
        return await _svc(request).fire(req.account_id, req.qq, req.text, now=req.now)
    except Exception as e:  # noqa: BLE001
        log.exception("主动私聊手动发送失败")
        return {"status": "failed", "reason": str(e)[:300]}


@router.post("/tick")
async def tick(req: TickIn, request: Request):
    try:
        return {
            "ok": True,
            "decisions": await _svc(request).tick(
                account_id=req.account_id, dry_run=req.dry_run, now=req.now
            ),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}


@router.get("/logs")
def logs(request: Request, limit: int = 20):
    db = request.app.state.db
    rows = db.query("SELECT * FROM proactive_log ORDER BY id DESC LIMIT ?", [limit])
    return {"ok": True, "logs": [dict(r) for r in rows]}
