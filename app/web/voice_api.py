"""语音接口。

- 看状态：开关 / 引擎 / 参考音 / 最近日志
- 管理参考音：列、增改、删
- 试听：给一段文字，直接合成 wav 返回（不管触发规则）
"""

from __future__ import annotations

import contextlib
import json
import logging
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ..adapters.tts.base import VoiceError
from ..voice import upsert_ref
from ..voice_engines import ENGINE_TYPES, delete_engine, list_engines, save_engine

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])


class RefIn(BaseModel):
    id: str
    account_id: str = ""
    persona_id: str = ""
    name: str = ""
    engine: str = ""
    audio_path: str = ""
    prompt_text: str = ""
    params: dict = Field(default_factory=dict)
    weight: float = 1.0
    tags: list[str] = Field(default_factory=list)
    min_chars: int = 0
    max_chars: int = 9999
    emotions: list[str] = Field(default_factory=list)
    condition: dict | None = None
    enabled: bool = True
    note: str = ""


class EngineIn(BaseModel):
    id: str
    account_id: str = ""
    name: str = ""
    type: str = "gptsovits"
    url: str = ""
    api_key: str = ""
    model: str = ""
    voice: str = ""
    timeout: float = 120
    defaults: dict = Field(default_factory=dict)
    enabled: bool = True


class PreviewIn(BaseModel):
    text: str
    account_id: str = ""
    chat_id: str = "preview"
    ref_id: str = ""
    emotion: str | None = None


def _db(request: Request):
    return request.app.state.db


def _voice(request: Request):
    v = getattr(request.app.state, "voice", None)
    if v is None:
        raise RuntimeError("语音服务没起来")
    return v


def _row(row) -> dict:
    d = dict(row)
    for key in ("params", "tags", "emotions"):
        try:
            d[key] = json.loads(d.get(key) or ("{}" if key == "params" else "[]"))
        except (TypeError, json.JSONDecodeError):
            d[key] = {} if key == "params" else []
    if d.get("condition"):
        try:
            d["condition"] = json.loads(d["condition"])
        except json.JSONDecodeError:
            d["condition"] = None
    d["enabled"] = bool(d.get("enabled"))
    return d


@router.get("/status")
def status(request: Request, account_id: str = ""):
    db = _db(request)
    from ..settings import Settings
    convert_rate = 100
    reply_mode = "both"
    try:
        st = Settings(db, account_id=account_id)
        engines = [e["id"] for e in list_engines(db, account_id)]
        enabled = bool(st.get("voice.enabled")) and bool(engines)
        convert_rate = int(st.get("voice.convert_rate") or 0)
        reply_mode = str(st.get("voice.reply_mode") or "both")
    except Exception:  # noqa: BLE001
        engines, enabled = [], False

    refs = db.scalar("SELECT COUNT(*) FROM voice_refs WHERE enabled = 1 AND account_id = ?", [account_id], default=0)
    logs = [dict(r) for r in db.query(
        "SELECT id, account_id, ref_id, engine, text, text_len, duration, ok, error, created_at "
        "FROM voice_logs WHERE account_id = ? ORDER BY id DESC LIMIT 10",
        [account_id],
    )]
    return {
        "ok": True,
        "enabled": enabled,
        "engines": engines,
        "refs": refs,
        "convert_rate": convert_rate,
        "reply_mode": reply_mode,
        "logs": logs,
    }


@router.get("/refs")
def list_refs(request: Request, persona_id: str = "", account_id: str = ""):
    db = _db(request)
    if persona_id:
        rows = db.query(
            "SELECT * FROM voice_refs WHERE account_id = ? AND (persona_id = ? OR persona_id = '') "
            "ORDER BY weight DESC",
            [account_id, persona_id],
        )
    else:
        rows = db.query(
            "SELECT * FROM voice_refs WHERE account_id = ? ORDER BY persona_id, weight DESC",
            [account_id],
        )
    return {"ok": True, "refs": [_row(r) for r in rows]}


@router.post("/refs")
def save_ref(req: RefIn, request: Request):
    db = _db(request)
    try:
        upsert_ref(db, req.model_dump())
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}
    row = db.query_one("SELECT * FROM voice_refs WHERE id = ?", [req.id])
    return {"ok": True, "ref": _row(row) if row else None}


@router.delete("/refs/{ref_id}")
def delete_ref(request: Request, ref_id: str, account_id: str = ""):
    db = _db(request)
    if account_id:
        n = db.execute("DELETE FROM voice_refs WHERE id = ? AND account_id = ?", [ref_id, account_id])
    else:
        n = db.execute("DELETE FROM voice_refs WHERE id = ?", [ref_id])
    return {"ok": n > 0}


@router.get("/engines")
def engines(request: Request, account_id: str = ""):
    return {
        "ok": True,
        "types": [{"id": k, "name": v} for k, v in ENGINE_TYPES.items()],
        "engines": list_engines(_db(request), account_id),
    }


@router.post("/engines")
async def save_engine_api(req: EngineIn, request: Request):
    db = _db(request)
    try:
        row = save_engine(db, req.model_dump())
        if req.account_id:
            from ..settings import Settings
            st = Settings(db, account_id=req.account_id)
            st.set("voice.default_engine", req.id)
            if req.id == "gptsovits" and req.url:
                st.set("voice.engine_url", req.url)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    try:
        voice = _voice(request)
        await voice.reload_engines()
    except Exception as e:  # noqa: BLE001
        log.warning("重建语音池失败：%s", e)
    return {"ok": True, "engine": row}


@router.delete("/engines/{engine_id}")
async def delete_engine_api(request: Request, engine_id: str, account_id: str = ""):
    ok = delete_engine(_db(request), engine_id)
    try:
        voice = _voice(request)
        await voice.reload_engines()
    except Exception as e:  # noqa: BLE001
        log.warning("重建语音池失败：%s", e)
    return {"ok": ok}


@router.post("/engines/{engine_id}/health")
async def engine_health(request: Request, engine_id: str, account_id: str = ""):
    from .. import config as _config
    from ..voice_engines import build_pool_from_db

    pool = build_pool_from_db(_db(request), _config.get(), account_id)
    engine = pool.get(engine_id)
    if engine is None:
        return {"ok": False, "error": "引擎没启用或不存在"}
    try:
        return {"ok": True, "healthy": bool(await engine.health())}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:160]}
    finally:
        with contextlib.suppress(Exception):
            await pool.close()


@router.get("/engines/health")
async def engines_health(request: Request, account_id: str = ""):
    from .. import config as _config
    from ..voice_engines import build_pool_from_db

    pool = build_pool_from_db(_db(request), _config.get(), account_id)
    out = {}
    try:
        for name in pool.names():
            engine = pool.get(name)
            out[name] = bool(await engine.health()) if engine else False
    finally:
        with contextlib.suppress(Exception):
            await pool.close()
    return {"ok": True, "engines": out}


@router.post("/preview")
async def preview(req: PreviewIn, request: Request):
    try:
        from .. import config as _config
        from ..settings import Settings
        from ..voice import VoiceService

        voice = VoiceService(
            _db(request), getattr(request.app.state, "persona", None), _config.get(),
            Settings(_db(request), account_id=req.account_id),
        )
        wav, meta = await voice.preview(
            text=req.text,
            account_id=req.account_id,
            chat_id=req.chat_id,
            ref_id=req.ref_id,
            emotion=req.emotion,
        )
    except RuntimeError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)
    except VoiceError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception as e:  # noqa: BLE001
        log.exception("试听失败")
        return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=500)

    # header 只能是 latin-1，中文名字要 percent-encode
    headers = {
        "X-Voice-Ref": quote(str(meta.get("ref", ""))),
        "X-Voice-Ref-Id": str(meta.get("ref_id", "")),
        "X-Voice-Engine": str(meta.get("engine", "")),
        "X-Voice-Duration": str(meta.get("duration", "")),
        "X-Voice-Reason": quote(str(meta.get("reason", ""))),
    }
    return Response(content=wav, media_type="audio/wav", headers=headers)
