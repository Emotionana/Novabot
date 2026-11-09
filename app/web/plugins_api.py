"""插件下载 API：版本、下载源、下载任务。"""

from __future__ import annotations

import contextlib

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..plugin_downloader import SOURCES, PluginDownloader

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


def _svc(request: Request) -> PluginDownloader:
    svc = getattr(request.app.state, "plugin_downloader", None)
    if svc is None:
        from ..settings import Settings

        svc = PluginDownloader(request.app.state.db, Settings(request.app.state.db))
        request.app.state.plugin_downloader = svc
    return svc


class DownloadIn(BaseModel):
    tool: str
    tag: str
    asset: str
    source: str = "auto"


class SwitchIn(BaseModel):
    tool: str = "snowluma"
    version: str


@router.get("")
async def catalog(request: Request):
    svc = _svc(request)
    return {"ok": True, "tools": svc.tools(), "sources": SOURCES}


@router.post("/switch")
async def switch(req: SwitchIn, request: Request):
    try:
        return await _svc(request).switch(req.tool, req.version)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}


@router.get("/{tool}/releases")
async def releases(tool: str, request: Request, refresh: int = 0):
    try:
        rows = await _svc(request).releases(tool, refresh=bool(refresh))
        return {"ok": True, "releases": rows}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300], "releases": []}


@router.post("/download")
async def download(req: DownloadIn, request: Request):
    return _svc(request).start(
        tool=req.tool, tag=req.tag, asset_name=req.asset, source=req.source,
    )


@router.get("/jobs/{job_id}")
async def job(job_id: str, request: Request):
    j = _svc(request).jobs.get(job_id)
    if j is None:
        return {"ok": False, "error": "任务不存在"}
    return {"ok": True, "job": j}


@router.post("/jobs/{job_id}/cancel")
async def cancel(job_id: str, request: Request):
    with contextlib.suppress(Exception):
        return _svc(request).cancel(job_id)
    return {"ok": False, "error": "取消失败"}
