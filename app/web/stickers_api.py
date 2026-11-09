"""表情包 API：每个账号独立库。"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Annotated

import httpx
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..settings import Settings
from ..stickers import StickerService

router = APIRouter(prefix="/api/stickers", tags=["stickers"])


def _llm(request: Request, account_id: str):
    gw = getattr(request.app.state, "gateway", None)
    rt = gw.get(account_id) if gw is not None else None
    return getattr(getattr(rt, "pipeline", None), "llm", None)


def _svc(request: Request, account_id: str = "") -> StickerService:
    db = request.app.state.db
    acc = account_id or request.query_params.get("account_id", "")
    st = Settings(db, account_id=acc)
    return StickerService(db, st, acc)


_ANALYZE_JOBS: dict[str, dict] = {}


@router.post("/analyze_all")
async def analyze_all(request: Request, account_id: str = ""):
    svc = _svc(request, account_id)
    key = account_id or svc.account_id
    current = _ANALYZE_JOBS.get(key)
    if current and current.get("status") == "running":
        return {"ok": True, "job": current}
    total = len([x for x in svc.list() if x.get("file")])
    job = {
        "status": "running", "total": total, "done": 0, "failed": 0,
        "current": "", "started_at": time.time(), "finished_at": 0,
    }
    _ANALYZE_JOBS[key] = job
    llm = _llm(request, account_id)

    def progress(done: int, total_: int, failed: int, name: str) -> None:
        job.update({"done": done, "total": total_, "failed": failed, "current": name})

    async def run() -> None:
        try:
            result = await svc.analyze_all(llm, progress=progress)
            job.update({
                "status": "done", "done": int(result.get("done") or 0),
                "failed": int(result.get("failed") or 0),
                "finished_at": time.time(),
            })
        except Exception as e:  # noqa: BLE001
            job.update({"status": "error", "error": str(e)[:300], "finished_at": time.time()})

    asyncio.create_task(run())
    return {"ok": True, "job": job}


@router.get("/analyze_all/status")
def analyze_all_status(account_id: str = ""):
    return {"ok": True, "job": _ANALYZE_JOBS.get(account_id or "")}


@router.get("")
def list_stickers(request: Request, account_id: str = "", q: str = "", scope: str = ""):
    return {"ok": True, "items": _svc(request, account_id).list(q, scope)}


@router.get("/status")
def status(request: Request, account_id: str = ""):
    return _svc(request, account_id).status()


@router.get("/image/{md5}")
def image(md5: str, request: Request, account_id: str = ""):
    p = _svc(request, account_id).image(md5)
    if p is None:
        return {"ok": False, "error": "没有这张表情"}
    mime = {"png": "image/png", "gif": "image/gif", "webp": "image/webp"}.get(p.suffix.lstrip("."), "image/jpeg")
    return FileResponse(str(p), media_type=mime, filename=p.name)


@router.post("/upload")
async def upload(
    request: Request,
    file: Annotated[UploadFile, File()],
    name: Annotated[str, Form()] = "",
    tags: Annotated[str, Form()] = "",
    scope: Annotated[str, Form()] = "owner_only",
    account_id: Annotated[str, Form()] = "",
):
    data = await file.read()
    tag_list = [x.strip() for x in tags.replace("，", ",").split(",") if x.strip()]
    svc = _svc(request, account_id)
    r = svc.add_bytes(data, name=name, tags=tag_list, source="panel", scope=scope)
    llm = _llm(request, account_id)
    if r.get("ok") and not r.get("existed"):
        asyncio.create_task(svc.analyze(r["item"]["md5"], llm))
    return r


class ImportQQIn(BaseModel):
    account_id: str = ""
    count: int = 120
    scope: str = "shared"


@router.post("/import_qq")
async def import_qq(req: ImportQQIn, request: Request):
    """读取当前 QQ 账号收藏的表情，下载入库并补自动打标。"""
    gw = getattr(request.app.state, "gateway", None)
    rt = gw.get(req.account_id or request.query_params.get("account_id", "")) if gw is not None else None
    client = getattr(rt, "client", None)
    if client is None or not getattr(client, "connected", False):
        return {"ok": False, "error": "这个 QQ 还没连上，无法读取收藏表情"}
    count = max(1, min(int(req.count or 120), 500))
    try:
        resp = await client.call("fetch_custom_face_detail", {"count": count}, timeout=45)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"读取收藏表情失败：{e}"}
    details = resp.get("data") or []
    if not isinstance(details, list):
        return {"ok": False, "error": "协议端返回格式不对"}
    svc = _svc(request, req.account_id)
    added = skipped = failed = 0
    created: list[str] = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        for d in details:
            try:
                url = str(d.get("url") or "")
                if not url:
                    failed += 1
                    continue
                if url.startswith("base64://"):
                    data = base64.b64decode(url[9:])
                else:
                    r = await c.get(url, headers={"User-Agent": "Novabot/0.1"})
                    r.raise_for_status()
                    data = r.content
                desc = str(d.get("desc") or "").strip()
                r = svc.add_bytes(
                    data, name=desc[:12] or "QQ表情", tags=[desc] if desc else [],
                    source="qq_favorite", scope=req.scope or "shared",
                )
                if not r.get("ok"):
                    failed += 1
                    continue
                md5 = str(r["item"].get("md5") or "")
                svc.set_emoji(md5, str(d.get("emoji_id") or ""))
                if r.get("existed"):
                    skipped += 1
                else:
                    added += 1
                    created.append(md5)
            except Exception:  # noqa: BLE001
                failed += 1
    if created:
        asyncio.create_task(svc.analyze_pending(_llm(request, req.account_id)))
    return {
        "ok": True, "total": len(details), "added": added,
        "skipped": skipped, "failed": failed, "analyzing": len(created),
    }


class UpdateIn(BaseModel):
    account_id: str = ""
    name: str = ""
    tags: list[str] = []
    scope: str = ""
    enabled: bool | None = None
    meaning: str = ""
    emotion: str = ""
    use_when: str = ""
    avoid_when: str = ""


@router.post("/{md5}/update")
def update(md5: str, req: UpdateIn, request: Request):
    return _svc(request, req.account_id).update(
        md5, name=req.name, tags=req.tags, scope=req.scope, enabled=req.enabled,
        meaning=req.meaning, emotion=req.emotion,
        use_when=req.use_when, avoid_when=req.avoid_when,
    )


@router.post("/{md5}/analyze")
async def analyze(md5: str, request: Request, account_id: str = ""):
    svc = _svc(request, account_id)
    return await svc.analyze(md5, _llm(request, account_id))


@router.delete("/{md5}")
def delete(md5: str, request: Request, account_id: str = ""):
    return _svc(request, account_id).delete(md5)
