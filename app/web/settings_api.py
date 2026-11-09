"""设置接口。

/api/settings/*

给界面读写参数用。参数定义在 app/params.py，这里只做转发和分组。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

log = logging.getLogger(__name__)

# 确保参数注册表被加载
import app.params  # noqa: F401,E402

router = APIRouter(prefix="/api/settings", tags=["settings"])

# 连接插件是整台机器一套，不能按 bot 分开；这些 key 始终写全局
GLOBAL_KEYS = {
    "runtime.master_enabled",
    "runtime.dry_run",
    "runtime.setup_done",
    "gateway.backend_autostart",
    "gateway.backend_command",
    "gateway.backend_workdir",
    "gateway.backend_check_port",
}



class SetReq(BaseModel):
    key: str
    value: object = None
    account_id: str = ""


class SetManyReq(BaseModel):
    values: dict = {}
    account_id: str = ""


def _settings(request: Request, account_id: str = ""):
    from ..settings import Settings

    return Settings(request.app.state.db, account_id=account_id)


@router.get("/schema")
async def schema(request: Request):
    """所有参数的定义（给界面渲染用）。"""
    from ..settings import by_group

    groups = by_group()
    return {
        "ok": True,
        "groups": [
            {
                "name": name,
                "params": [p.to_dict() for p in params if not p.hidden],
                "basic_count": sum(1 for p in params if p.level == "basic" and not p.hidden),
                "advanced_count": sum(1 for p in params if p.level == "advanced" and not p.hidden),
            }
            for name, params in groups.items()
        ],
    }


@router.get("/values")
async def values(request: Request, account_id: str = "", level: str = ""):
    """当前值。"""
    s = _settings(request, account_id)
    lvl = level if level in ("basic", "advanced") else None
    return {"ok": True, "values": s.get_all(level=lvl)}


@router.get("/syncable")
async def syncable(request: Request, account_id: str = ""):
    """哪些分组可以安全同步到其他 bot。"""
    from ..settings_sync import _targets, available_groups

    db = request.app.state.db
    return {
        "ok": True,
        "groups": available_groups(),
        "targets": [{"id": i, "name": n} for i, n in _targets(db, account_id)],
    }


class SyncReq(BaseModel):
    account_id: str = ""
    groups: list[str] = []
    targets: list[str] = []


@router.post("/sync")
async def sync(req: SyncReq, request: Request):
    """把通用行为同步到其他启用的 bot；接口/端口/凭据不在白名单里。"""
    from ..settings_sync import sync_settings

    return sync_settings(request.app.state.db, req.account_id, req.groups, req.targets)


@router.post("/set")
async def set_one(req: SetReq, request: Request):
    s = _settings(request, "" if req.key in GLOBAL_KEYS else req.account_id)
    try:
        v = s.set(req.key, req.value)
        return {"ok": True, "key": req.key, "value": v}
    except KeyError:
        return {"ok": False, "error": f"未知参数：{req.key}"}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@router.post("/set_many")
async def set_many(req: SetManyReq, request: Request):
    global_values = {k: v for k, v in req.values.items() if k in GLOBAL_KEYS}
    local_values = {k: v for k, v in req.values.items() if k not in GLOBAL_KEYS}
    out = _settings(request, "").set_many(global_values) if global_values else {}
    if local_values:
        out.update(_settings(request, req.account_id).set_many(local_values))
    return {"ok": True, "applied": out}


@router.post("/reset")
async def reset(request: Request, key: str = "", account_id: str = ""):
    s = _settings(request, "" if key in GLOBAL_KEYS else account_id)
    s.reset(key or None)
    return {"ok": True}


@router.get("/changed")
async def changed(request: Request, account_id: str = ""):
    """哪些参数被改过。"""
    from ..settings import all_params

    s = _settings(request, account_id)
    return {
        "ok": True,
        "changed": [k for k in all_params() if s.is_custom(k)],
    }
