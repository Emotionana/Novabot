"""额外模块 API：目前提供 B 站翻唱。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..song_module import SongModule

router = APIRouter(prefix="/api/modules", tags=["modules"])


def _svc(request: Request, account_id: str = "") -> SongModule:
    cache = getattr(request.app.state, "song_modules", None)
    if cache is None:
        cache = {}
        request.app.state.song_modules = cache
    svc = cache.get(account_id)
    if svc is None:
        from ..settings import Settings

        svc = SongModule(request.app.state.db, Settings(request.app.state.db, account_id=account_id), account_id)
        cache[account_id] = svc
    return svc


class SongJobIn(BaseModel):
    account_id: str = ""
    song_id: int | str
    song: str = ""
    voice: str = ""
    pitch: int = 0
    acapella: bool = False
    extract_only: bool = False


@router.get("")
async def modules(request: Request, account_id: str = ""):
    svc = _svc(request, account_id)
    song = svc.status()
    return {
        "ok": True,
        "modules": [
            {
                "id": "song", "name": "B站翻唱", "version": "0.1",
                "status": "ready" if song["ready"] else ("available" if song["enabled"] else "off"),
                "message": song["message"],
                "tags": ["B站", "音频提取", "RVC"],
            },
        ],
    }


@router.get("/song/status")
async def song_status(request: Request, account_id: str = ""):
    return _svc(request, account_id).status()


@router.get("/song/search")
async def song_search(request: Request, q: str, limit: int = 8, account_id: str = ""):
    try:
        return {"ok": True, "songs": await _svc(request, account_id).search(q, max(1, min(limit, 20)))}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300], "songs": []}


@router.post("/song/jobs")
async def song_job(req: SongJobIn, request: Request):
    return _svc(request, req.account_id).start(
        song_id=req.song_id, name=req.song, voice=req.voice,
        pitch=req.pitch, acapella=req.acapella, extract_only=req.extract_only,
    )


@router.get("/song/jobs/{job_id}")
async def song_job_status(job_id: str, request: Request, account_id: str = ""):
    job = _svc(request, account_id).jobs.get(job_id)
    if job is None:
        return {"ok": False, "error": "任务不存在"}
    return {"ok": True, "job": job}


@router.post("/song/voices")
async def upload_voice(
    request: Request,
    file: Annotated[UploadFile, File()],
    name: Annotated[str, Form()] = "",
    account_id: Annotated[str, Form()] = "",
):
    data = await file.read()
    if len(data) > 1024 * 1024 * 1024:
        return {"ok": False, "error": "文件超过 1 GB"}
    return _svc(request, account_id).import_voice(name or file.filename or "voice.pth", data)


@router.delete("/song/voices/{voice_id}")
async def delete_voice(voice_id: str, request: Request, account_id: str = ""):
    return _svc(request, account_id).delete_voice(voice_id)


@router.get("/song/files/{name}")
async def song_file(name: str, request: Request, account_id: str = ""):
    p = _svc(request, account_id).file(name)
    if p is None:
        return {"ok": False, "error": "文件不存在"}
    return FileResponse(str(p), media_type="audio/mp4" if p.suffix == ".m4a" else "audio/wav", filename=Path(p).name)
