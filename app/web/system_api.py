"""模块自检接口。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..system_status import collect, summary

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/system", tags=["system"])


def _spawn_stop_script() -> None:
    """调用停止脚本，杀掉父进程/子进程，不留后台 Python。"""
    root = Path(__file__).resolve().parents[2]
    script = root / "停止Novabot.bat"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", str(script)],
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
    except Exception as e:  # noqa: BLE001
        log.error("启动停止脚本失败：%s", e)
        os._exit(1)


async def _stop_later(delay: float = 0.5) -> None:
    await asyncio.sleep(delay)
    _spawn_stop_script()


async def _ui_watchdog(state) -> None:
    """页面关闭信号优先；心跳停止 15 秒兜底。"""
    while True:
        await asyncio.sleep(1)
        now = time.time()
        pending = float(getattr(state, "pagehide_shutdown_at", 0) or 0)
        if pending and now >= pending:
            log.warning("页面关闭信号超过宽限时间，关闭 Novabot")
            await _stop_later(0.1)
            return
        last = float(getattr(state, "ui_last_beat", 0) or 0)
        if last and now - last > 30:
            log.warning("前端心跳超过 30 秒，关闭 Novabot")
            await _stop_later(0.1)
            return


@router.post("/heartbeat")
async def heartbeat(request: Request):
    state = request.app.state
    state.ui_last_beat = time.time()
    state.pagehide_shutdown_at = 0
    task = getattr(state, "ui_watchdog", None)
    if task is None or task.done():
        state.ui_watchdog = asyncio.create_task(_ui_watchdog(state))
    return {"ok": True}


@router.post("/pagehide")
async def pagehide(request: Request):
    """关闭窗口时页面先 pagehide；给刷新留 4 秒，新页面心跳会撤销。"""
    state = request.app.state
    state.pagehide_shutdown_at = time.time() + 4
    task = getattr(state, "ui_watchdog", None)
    if task is None or task.done():
        state.ui_watchdog = asyncio.create_task(_ui_watchdog(state))
    return {"ok": True}


@router.post("/shutdown")
async def shutdown():
    asyncio.create_task(_stop_later())
    return {"ok": True}


@router.get("/safety")
def safety(request: Request):
    from ..settings import Settings

    st = Settings(request.app.state.db)
    return {
        "ok": True,
        "master_enabled": bool(st.get("runtime.master_enabled")),
        "dry_run": bool(st.get("runtime.dry_run")),
        "setup_done": bool(st.get("runtime.setup_done")),
        "owner_qq": str(st.get("security.owner_qq") or ""),
    }


class OwnerIn(BaseModel):
    qq: str


@router.post("/owner")
def set_owner(req: OwnerIn, request: Request):
    """改主人账号：同时写全局、所有账号、人物归属和运行中的 pipeline。"""
    from ..settings import Settings

    qq = str(req.qq or "").strip()
    if not qq:
        return {"ok": False, "error": "主人 QQ 不能为空"}
    db = request.app.state.db
    Settings(db).set("security.owner_qq", qq)
    rows = db.query("SELECT id FROM accounts")
    for row in rows:
        Settings(db, account_id=str(row["id"])).set("security.owner_qq", qq)
    with contextlib.suppress(Exception):
        db.execute("UPDATE people SET is_owner = CASE WHEN qq = ? THEN 1 ELSE 0 END", [qq])
    gw = getattr(request.app.state, "gateway", None)
    if gw is not None:
        for rt in getattr(gw, "accounts", {}).values():
            pipeline = getattr(rt, "pipeline", None)
            if pipeline is not None:
                pipeline.owner_qq = qq
    return {"ok": True, "owner_qq": qq}


@router.get("/backups")
def backups(request: Request):
    from ..backup_service import list_backups

    data_dir = Path(getattr(request.app.state.db, "path", "data/novabot.db")).parent
    return {"ok": True, "items": list_backups(data_dir)}


@router.post("/backups")
def make_backup(request: Request):
    from ..backup_service import create_backup

    data_dir = Path(getattr(request.app.state.db, "path", "data/novabot.db")).parent
    try:
        return create_backup(request.app.state.db, data_dir=data_dir)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}


@router.get("/backups/{name}")
def download_backup(name: str, request: Request):
    from ..backup_service import backup_file

    data_dir = Path(getattr(request.app.state.db, "path", "data/novabot.db")).parent
    p = backup_file(data_dir, name)
    if p is None:
        return {"ok": False, "error": "备份不存在"}
    return FileResponse(str(p), media_type="application/zip", filename=p.name)


@router.delete("/backups/{name}")
def delete_backup(name: str, request: Request):
    from ..backup_service import backup_file

    data_dir = Path(getattr(request.app.state.db, "path", "data/novabot.db")).parent
    p = backup_file(data_dir, name)
    if p is None:
        return {"ok": False, "error": "备份不存在"}
    p.unlink(missing_ok=True)
    return {"ok": True}


@router.post("/backups/restore")
async def restore_backup(request: Request, file: Annotated[UploadFile, File()]):
    from ..backup_service import stage_restore

    data_dir = Path(getattr(request.app.state.db, "path", "data/novabot.db")).parent
    suffix = Path(file.filename or "backup.zip").suffix or ".zip"
    fd, tmp_name = tempfile.mkstemp(prefix="novabot-restore-", suffix=suffix)
    try:
        import os

        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        return stage_restore(Path(tmp_name), data_dir)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}
    finally:
        Path(tmp_name).unlink(missing_ok=True)


@router.get("/modules")
def modules(request: Request, account_id: str = ""):
    rows = collect(request.app.state.db, account_id)
    return {"ok": True, "summary": summary(rows), "modules": rows}


class ModelTestIn(BaseModel):
    account_id: str = ""
    prompt: str = "只回复四个字：模型已通"


@router.get("/model")
def model_status(request: Request):
    """给界面看的有效模型状态：配置 + 运行中的 pipeline 实际挂载的适配器。"""
    from .. import config
    from ..adapters.llm.agent import AgentAdapter
    from ..settings import Settings

    db = request.app.state.db
    st = Settings(db)
    gw = getattr(request.app.state, "gateway", None)
    accounts = []
    if gw is not None:
        for acc_id, rt in getattr(gw, "accounts", {}).items():
            llm = getattr(getattr(rt, "pipeline", None), "llm", None)
            accounts.append({
                "id": acc_id,
                "model": type(llm).__name__ if llm else "",
                "endpoint": getattr(llm, "endpoint", "") if isinstance(llm, AgentAdapter) else "",
            })
    cfg = config.get()
    return {
        "ok": True,
        "agent_enabled": bool(st.get("llm.agent_enabled")),
        "agent_type": str(st.get("llm.agent_type") or "dsh"),
        "agent_endpoint": str(st.get("llm.agent_endpoint") or ""),
        "default": cfg.llm.default or "",
        "accounts": accounts,
    }


@router.get("/media/models")
async def media_models():
    """检索本机可选的视觉模型，供图片/视频分别自选。"""
    from ..media_detect import list_local_vision_models

    models = await list_local_vision_models()
    return {"ok": True, "models": models, "count": len(models)}


@router.post("/media/detect")
async def media_detect(request: Request):
    """重新探测本地视觉模型，并写入当前账号。"""
    from ..media_detect import apply_detected_vision

    return await apply_detected_vision(request.app.state.db)


@router.post("/model/detect")
async def model_detect(request: Request, account_id: str = ""):
    """探测本机 DSH Agent 并自动写入账号配置。"""
    from ..model_detect import detect_and_apply

    gw = getattr(request.app.state, "gateway", None)

    async def refresh():
        if gw is None:
            return
        for rt in getattr(gw, "accounts", {}).values():
            with contextlib.suppress(Exception):
                await gw._ensure_llm(rt)

    result = await detect_and_apply(request.app.state.db, refresh=refresh)
    if account_id:
        result["account_id"] = account_id
    return result


@router.post("/model/test")
async def model_test(request: Request, req: ModelTestIn):
    """在真实适配器上发一句，不写记忆、不发 QQ；用来判断 DSH 是否真的能回。"""
    import time

    from .. import config
    from ..adapters.llm.factory import build_llm
    from ..settings import Settings

    db = request.app.state.db
    account_id = req.account_id
    if not account_id:
        gw = getattr(request.app.state, "gateway", None)
        accounts = list(getattr(gw, "accounts", {}) or {})
        account_id = accounts[0] if accounts else ""
    llm = build_llm(config.get(), Settings(db, account_id=account_id), account_id)
    if llm is None:
        return {"ok": False, "error": "没有可用模型：检查 Agent 开关、地址和 DSH 是否在线"}
    started = time.time()
    try:
        reply = await llm.chat(
            system="你是 Novabot 的连通性测试，只按用户要求回复，不调用工具。",
            messages=[{"role": "user", "content": req.prompt}],
            max_tokens=300,
            session_id=f"model-test:{account_id or 'global'}",
            account_id=account_id,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}
    finally:
        closer = getattr(llm, "close", None)
        if callable(closer):
            with contextlib.suppress(Exception):
                await closer()
    return {
        "ok": bool(reply.text),
        "reply": (reply.text or "")[:1000],
        "error": reply.error or "",
        "ms": round((time.time() - started) * 1000),
    }
