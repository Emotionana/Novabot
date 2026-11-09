"""文件传输 API：沙箱列表、上传、审批、发送。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..file_service import FileService

router = APIRouter(prefix="/api/files", tags=["files"])


def _svc(request: Request, account_id: str = "") -> FileService:
    from ..settings import Settings

    return FileService(request.app.state.db, Settings(request.app.state.db, account_id=account_id), account_id)


class RequestIn(BaseModel):
    name: str
    chat_id: str = ""
    account_id: str = ""
    sender: str = "panel"


@router.get("/status")
def status(request: Request, account_id: str = ""):
    return _svc(request, account_id).status()


@router.get("/list")
def list_files(request: Request, box: str = "outbox", account_id: str = ""):
    return {"ok": True, "files": _svc(request, account_id).list(box)}


@router.post("/upload")
async def upload(
    request: Request,
    file: Annotated[UploadFile, File()],
    account_id: Annotated[str, Form()] = "",
):
    data = await file.read()
    if len(data) > 200 * 1024 * 1024:
        return {"ok": False, "error": "文件太大"}
    return _svc(request, account_id).upload(file.filename or "file", data)


@router.get("/pending")
def pending(request: Request, account_id: str = ""):
    return {"ok": True, "pending": _svc(request, account_id).pending()}


@router.post("/request")
async def make_request(req: RequestIn, request: Request):
    svc = _svc(request, req.account_id)
    account = req.account_id or ""
    chat_id = req.chat_id
    if not chat_id:
        from ..settings import Settings

        owner = str(Settings(request.app.state.db, account_id=account).get("security.owner_qq") or "")
        chat_id = f"private:{owner}" if owner else ""
    r = svc.request(req.name, chat_id=chat_id, account_id=account, sender=req.sender)
    if not r.get("ok"):
        return r
    if not svc._get("files.require_approval", True):
        gw = getattr(request.app.state, "gateway", None)
        try:
            if gw is not None:
                await gw.send_file_to_chat(account, chat_id, r.pop("path", ""), r.get("name", ""))
                svc.mark_sent(r.get("id", ""))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"发送失败：{e}"}
    return r


@router.post("/pending/{req_id}/approve")
async def approve(req_id: str, request: Request):
    svc = _svc(request, request.query_params.get("account_id", ""))
    r = svc.approve(req_id)
    if not r.get("ok"):
        return r
    gw = getattr(request.app.state, "gateway", None)
    if gw is None:
        return {"ok": False, "error": "网关没起来"}
    try:
        await gw.send_file_to_chat(r["account_id"], r["chat_id"], r["path"], r["name"])
        svc.mark_sent(req_id)
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}


@router.post("/pending/{req_id}/reject")
def reject(req_id: str, request: Request, account_id: str = ""):
    return _svc(request, account_id).reject(req_id)


@router.get("/download")
def download(request: Request, box: str = "outbox", name: str = "", account_id: str = ""):
    svc = _svc(request, account_id)
    root = svc.inbox if box == "inbox" else svc.outbox
    p = (root / Path(name).name).resolve()
    if root.resolve() not in p.parents or not p.is_file():
        return {"ok": False, "error": "文件不存在"}
    return FileResponse(str(p), filename=p.name)
