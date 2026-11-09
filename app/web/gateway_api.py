"""网关管理接口。

/api/gateway/*

账号增删、状态查询、手动重连。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/gateway", tags=["gateway"])


class AddAccountReq(BaseModel):
    id: str
    bot_qq: str = ""
    url: str = ""
    listen: str = ""
    token: str = ""
    impl: str = "snowluma"
    reverse: bool = False


def _manager(request: Request):
    m = getattr(request.app.state, "gateway", None)
    if m is None:
        raise RuntimeError("网关没启用")
    return m


def _accounts_for_status(request: Request, m) -> list[dict]:
    """把已配置账号和正在运行的账号合并，未启动的也显示出来。"""
    runtime = {r["id"]: r for r in m.list_status()}
    try:
        from .accounts_api import AccountStore

        configured = AccountStore(request.app.state.db).list()
    except Exception:  # noqa: BLE001
        configured = []
    accounts = []
    for a in configured:
        row = runtime.pop(a["id"], None) or {
            "id": a["id"], "bot_qq": a.get("bot_qq") or "", "enabled": a.get("enabled", False),
            "connected": False, "received": 0, "sent": 0, "errors": 0,
            "last_message_at": 0, "last_error": "", "failures": 0, "dropped": 0,
        }
        row["name"] = a.get("name") or a["id"]
        row["auto_start"] = bool(a.get("auto_start"))
        row["url"] = a.get("url") or ""
        row["configured"] = bool(a.get("bot_qq") and (a.get("url") or a.get("onebot_port")))
        accounts.append(row)
    accounts.extend(runtime.values())
    return accounts


@router.get("/status")
async def status(request: Request):
    try:
        m = _manager(request)
    except RuntimeError:
        return {"ok": True, "enabled": False, "accounts": [], "summary": {}}
    return {
        "ok": True,
        # 注意：不是"网关对象存在"就算开，要看总开关真的开了没
        "enabled": bool(getattr(m, "active", False)),
        "accounts": _accounts_for_status(request, m),
        "summary": m.summary(),
    }


@router.get("/account/{acc_id}")
async def account_detail(request: Request, acc_id: str):
    m = _manager(request)
    rt = m.get(acc_id)
    if rt is None:
        return {"ok": False, "error": "没有这个账号"}
    return {"ok": True, "account": rt.status()}


@router.post("/account")
async def add_account(req: AddAccountReq, request: Request):
    m = _manager(request)
    try:
        rt = await m.add_account(req.id, req.model_dump())
        return {"ok": True, "account": rt.status()}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@router.delete("/account/{acc_id}")
async def remove_account(request: Request, acc_id: str):
    m = _manager(request)
    try:
        await m.remove_account(acc_id)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@router.post("/account/{acc_id}/retry")
async def retry(request: Request, acc_id: str):
    m = _manager(request)
    ok = await m.retry(acc_id)
    return {"ok": ok}


@router.get("/impls")
async def impls():
    """支持的 OneBot 实现列表（给界面下拉用）。"""
    from ..gateway.onebot import IMPL_PROFILES

    labels = {
        "snowluma": "SnowLuma",
        "napcat": "NapCat",
        "lagrange": "Lagrange.OneBot",
        "llonebot": "LLOneBot",
        "go-cqhttp": "go-cqhttp",
        "custom": "其他（自定义）",
    }
    return {
        "ok": True,
        "impls": [
            {"id": k, "name": labels.get(k, k), "profile": v}
            for k, v in IMPL_PROFILES.items()
        ],
    }
