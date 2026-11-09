"""平台适配的 HTTP 接口。

DSH 插件通过这几个接口调 Novabot：
  POST /api/platform/recall    取记忆
  POST /api/platform/remember  记对话
  POST /api/platform/persona   取人格
  POST /api/platform/map       映射会话
  GET  /api/platform/sessions  列出会话
  POST /api/platform/import    导入历史
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/platform", tags=["platform"])


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------


class RecallReq(BaseModel):
    platform: str = "dsh"
    session_id: str
    sender: str
    text: str = ""
    account_id: str = ""
    max_chars: int = Field(default=300, ge=50, le=3000)


class RememberReq(BaseModel):
    platform: str = "dsh"
    session_id: str
    sender: str
    text: str
    sender_nick: str = ""
    account_id: str = ""
    is_self: bool = False


class PersonaReq(BaseModel):
    persona_id: str = ""
    session_id: str = ""
    sender: str = ""
    text: str = ""


class MapReq(BaseModel):
    platform: str = "dsh"
    session_id: str
    account_id: str = ""
    title: str = ""


class ImportReq(BaseModel):
    platform: str = "dsh"
    session_id: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    account_id: str = ""


# ---------------------------------------------------------------------------
# 依赖
# ---------------------------------------------------------------------------


def _provider(request: Request):
    p = getattr(request.app.state, "platform_provider", None)
    if p is None:
        raise RuntimeError("平台适配没启用")
    return p


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@router.get("/detect")
async def detect(request: Request):
    from ..platform_probe import probe
    from ..settings import Settings

    cached = getattr(request.app.state, "platform_probe", None)
    if cached and time.time() - float(cached.get("at") or 0) < 10:
        return cached
    result = await probe(request.app.state.db, Settings(request.app.state.db))
    request.app.state.platform_probe = result
    return result


@router.post("/detect")
async def detect_now(request: Request):
    from ..platform_probe import probe
    from ..settings import Settings

    result = await probe(request.app.state.db, Settings(request.app.state.db))
    request.app.state.platform_probe = result
    return result


@router.post("/recall")
async def recall(req: RecallReq, request: Request):
    p = _provider(request)
    text = p.recall(
        platform=req.platform,
        external_id=req.session_id,
        sender_qq=req.sender,
        text=req.text,
        account_id=req.account_id,
        max_chars=req.max_chars,
    )
    return {"ok": True, "memory": text, "chars": len(text)}


@router.post("/remember")
async def remember(req: RememberReq, request: Request):
    p = _provider(request)
    ep_id = p.remember(
        platform=req.platform,
        external_id=req.session_id,
        sender_qq=req.sender,
        text=req.text,
        sender_nick=req.sender_nick,
        account_id=req.account_id,
        is_self=req.is_self,
    )
    return {"ok": ep_id is not None, "episode_id": ep_id}


@router.post("/persona")
async def persona(req: PersonaReq, request: Request):
    p = _provider(request)
    text = p.persona_prompt(
        persona_id=req.persona_id,
        chat_id=req.session_id,
        sender_qq=req.sender,
        text=req.text,
    )
    return {"ok": True, "prompt": text, "chars": len(text)}


@router.post("/map")
async def map_session(req: MapReq, request: Request):
    p = _provider(request)
    chat_id = p.map_session(
        req.platform, req.session_id,
        account_id=req.account_id, title=req.title,
    )
    return {"ok": True, "chat_id": chat_id}


@router.get("/sessions")
async def sessions(request: Request, platform: str | None = None, limit: int = 50):
    p = _provider(request)
    items = p.list_sessions(platform, limit)
    return {
        "ok": True,
        "sessions": [
            {
                "platform": s.platform,
                "session_id": s.external_id,
                "chat_id": s.chat_id,
                "title": s.title,
                "last_active": s.last_active,
            }
            for s in items
        ],
    }


@router.post("/import")
async def import_history(req: ImportReq, request: Request):
    p = _provider(request)
    n = p.import_history(
        platform=req.platform,
        external_id=req.session_id,
        messages=req.messages,
        account_id=req.account_id,
    )
    return {"ok": True, "imported": n}
