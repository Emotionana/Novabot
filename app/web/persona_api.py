"""人格卡接口：列表、读取、保存、启用、删除。"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..models import ExampleDialogue, PersonaCard, SpeechStyle, Trait
from ..persona import PersonaSystem
from ..settings import Settings

router = APIRouter(prefix="/api/persona", tags=["persona"])


class KeywordIn(BaseModel):
    id: str = ""
    keywords: list[str] = Field(default_factory=list)
    match_mode: str = "any"
    effects: list[dict] = Field(default_factory=list)
    chance: float = 1.0
    cooldown: int = 0
    priority: int = 0
    once_per_chat: bool = False
    enabled: bool = True
    note: str = ""


class BlockIn(BaseModel):
    id: int = 0
    title: str
    kind: str = "other"
    content: str
    sort_order: int = 0
    enabled: bool = True
    weight: float = 1.0
    note: str = ""


class CardIn(BaseModel):
    id: str = ""
    name: str = ""
    alias: list[str] = Field(default_factory=list)
    identity: str = ""
    background: str = ""
    traits: list[dict] = Field(default_factory=list)
    speech: dict = Field(default_factory=dict)
    boundaries: list[str] = Field(default_factory=list)
    examples: list[dict] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    avatar: str = ""
    account_id: str = ""
    activate: bool = True


class WakeIn(BaseModel):
    name: str = ""
    alias: list[str] = Field(default_factory=list)


def _active_card(request: Request, account_id: str = ""):
    s = Settings(_db(request), account_id=account_id)
    pid = str(s.get("persona.current") or "")
    if not pid:
        return None
    return PersonaSystem(_db(request), s).load_card(pid, account_id=account_id)


def _db(request: Request):
    return request.app.state.db


def _settings(request: Request, account_id: str = ""):
    return Settings(_db(request), account_id=account_id)


def _system(request: Request) -> PersonaSystem:
    return PersonaSystem(_db(request), _settings(request))


def _card(row) -> dict:
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "name": row["name"],
        "avatar": row["avatar"] or "",
        "updated_at": row["updated_at"],
    }


def _avatar_dir(request: Request):
    path = Path(_db(request).path).parent / "personas" / "avatars"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _avatar_path(request: Request, card_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", card_id)[:80]
    for ext in ("png", "jpg", "jpeg", "gif", "webp"):
        p = _avatar_dir(request) / f"{safe}.{ext}"
        if p.is_file():
            return p
    return _avatar_dir(request) / f"{safe}.img"


@router.post("/cards/{card_id}/avatar")
async def upload_avatar(card_id: str, request: Request, file: Annotated[UploadFile, File()]):
    data = await file.read()
    if not data or len(data) > 2 * 1024 * 1024:
        return {"ok": False, "error": "头像要小于 2MB"}
    from ..stickers import sniff_image

    mime = sniff_image(data)
    if not mime:
        return {"ok": False, "error": "只支持 jpg/png/gif/webp"}
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp"}[mime]
    safe = re.sub(r"[^A-Za-z0-9_-]", "", card_id)[:80]
    if not safe:
        return {"ok": False, "error": "卡片 id 不合法"}
    for old in _avatar_dir(request).glob(f"{safe}.*"):
        old.unlink(missing_ok=True)
    path = _avatar_dir(request) / f"{safe}.{ext}"
    path.write_bytes(data)
    import contextlib

    with contextlib.suppress(Exception):
        _db(request).execute("UPDATE persona_cards SET avatar = ?, updated_at = ? WHERE id = ?", [path.name, time.time(), card_id])
    return {"ok": True, "avatar": path.name}


@router.get("/cards/{card_id}/avatar")
def get_avatar(card_id: str, request: Request):
    p = _avatar_path(request, card_id)
    if not p.is_file():
        return {"ok": False, "error": "没有头像"}
    mime = {"png": "image/png", "gif": "image/gif", "webp": "image/webp"}.get(p.suffix.lstrip("."), "image/jpeg")
    return FileResponse(str(p), media_type=mime, filename=p.name)


@router.get("/cards")
def list_cards(request: Request, account_id: str = ""):
    db = _db(request)
    rows = db.query(
        "SELECT id, account_id, name, avatar, updated_at FROM persona_cards "
        "WHERE account_id = '' OR account_id = ? ORDER BY updated_at DESC",
        [account_id],
    )
    current = _settings(request, account_id).get("persona.current") or ""
    return {"ok": True, "cards": [_card(r) for r in rows], "current": current}


@router.get("/cards/{card_id}")
def get_card(request: Request, card_id: str):
    row = _db(request).query_one("SELECT * FROM persona_cards WHERE id = ?", [card_id])
    if row is None:
        return {"ok": False, "error": "人格卡不存在"}
    import json

    def loads(raw, default):
        try:
            return json.loads(raw or default)
        except Exception:  # noqa: BLE001
            return json.loads(default)

    return {
        "ok": True,
        "card": {
            "id": row["id"],
            "account_id": row["account_id"],
            "name": row["name"],
            "avatar": row["avatar"] or "",
            "alias": loads(row["alias"], "[]"),
            "identity": row["identity"] or "",
            "background": row["background"] or "",
            "traits": loads(row["traits"], "[]"),
            "speech": loads(row["speech"], "{}"),
            "boundaries": loads(row["boundaries"], "[]"),
            "examples": loads(row["examples"], "[]"),
            "tags": loads(row["tags"], "[]"),
            "version": row["version"] or 1,
        },
    }


@router.post("/cards")
def save_card(req: CardIn, request: Request):
    card_id = (req.id or "").strip().lower()
    if not card_id:
        import uuid

        card_id = "persona-" + uuid.uuid4().hex[:8]
    if not req.name.strip():
        return {"ok": False, "error": "人格卡名称不能为空"}
    try:
        traits = [Trait(name=str(t.get("name") or ""), weight=float(t.get("weight") or 0.5),
                        trigger=str(t.get("trigger") or "")) for t in req.traits if t.get("name")]
        speech = SpeechStyle(**{k: v for k, v in (req.speech or {}).items()
                                if k in {"length", "punctuation", "habits", "forbidden", "sass", "emoji_level"}})
        examples = [ExampleDialogue(user=str(e.get("user") or ""), assistant=str(e.get("assistant") or ""),
                                    note=e.get("note")) for e in req.examples if e.get("user") or e.get("assistant")]
        card = PersonaCard(
            id=card_id, account_id=req.account_id, name=req.name.strip(), avatar=req.avatar,
            alias=[str(x) for x in req.alias if str(x).strip()],
            identity=req.identity, background=req.background,
            traits=traits, speech=speech,
            boundaries=[str(x) for x in req.boundaries if str(x).strip()],
            examples=examples, tags=[str(x) for x in req.tags if str(x).strip()],
        )
        _system(request).save_card(card)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}
    if req.activate:
        _settings(request, req.account_id).set("persona.current", card_id)
    return {"ok": True, "id": card_id}


@router.post("/cards/{card_id}/activate")
def activate_card(request: Request, card_id: str, account_id: str = ""):
    row = _db(request).query_one("SELECT 1 FROM persona_cards WHERE id = ?", [card_id])
    if row is None:
        return {"ok": False, "error": "人格卡不存在"}
    _settings(request, account_id).set("persona.current", card_id)
    return {"ok": True}


@router.delete("/cards/{card_id}")
def delete_card(request: Request, card_id: str):
    row = _db(request).query_one("SELECT avatar FROM persona_cards WHERE id = ?", [card_id])
    if row is not None and row["avatar"]:
        p = _avatar_dir(request) / str(row["avatar"])
        if p.is_file():
            p.unlink(missing_ok=True)
    n = _db(request).execute("DELETE FROM persona_cards WHERE id = ?", [card_id])
    return {"ok": n > 0}


# ---------------------------------------------------------------------------
# 关键词 / 手写区
# ---------------------------------------------------------------------------


def _card_exists(request: Request, card_id: str) -> bool:
    return bool(_db(request).query_one("SELECT 1 FROM persona_cards WHERE id = ?", [card_id]))


@router.get("/cards/{card_id}/keywords")
def list_keywords(request: Request, card_id: str):
    rows = _db(request).query(
        "SELECT * FROM persona_keywords WHERE persona_id = ? ORDER BY priority DESC, id",
        [card_id],
    )
    out = []
    for r in rows:
        d = dict(r)
        for key in ("keywords", "effects"):
            try:
                d[key] = json.loads(d.get(key) or ("[]" if key != "effects" else "[]"))
            except Exception:  # noqa: BLE001
                d[key] = []
        d["enabled"] = bool(d.get("enabled"))
        out.append(d)
    return {"ok": True, "rules": out}


@router.post("/cards/{card_id}/keywords")
def save_keyword(request: Request, card_id: str, req: KeywordIn):
    if not _card_exists(request, card_id):
        return {"ok": False, "error": "人格卡不存在"}
    kws = [str(x).strip() for x in req.keywords if str(x).strip()]
    if not kws:
        return {"ok": False, "error": "关键词不能为空"}
    kid = (req.id or "").strip() or f"kw-{card_id}-{int(time.time() * 1000) % 1000000}"
    db = _db(request)
    db.execute(
        "INSERT OR REPLACE INTO persona_keywords "
        "(id, persona_id, keywords, match_mode, effects, chance, cooldown, priority, "
        " once_per_chat, enabled, note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [kid, card_id, json.dumps(kws, ensure_ascii=False), req.match_mode or "any",
         json.dumps(req.effects or [], ensure_ascii=False), float(req.chance or 1),
         int(req.cooldown or 0), int(req.priority or 0), 1 if req.once_per_chat else 0,
         1 if req.enabled else 0, req.note or ""],
    )
    return {"ok": True, "id": kid}


@router.delete("/keywords/{keyword_id}")
def delete_keyword(request: Request, keyword_id: str):
    return {"ok": _db(request).execute("DELETE FROM persona_keywords WHERE id = ?", [keyword_id]) > 0}


@router.get("/cards/{card_id}/blocks")
def list_blocks(request: Request, card_id: str):
    rows = _db(request).query(
        "SELECT * FROM persona_raw_blocks WHERE persona_id = ? ORDER BY sort_order, id",
        [card_id],
    )
    return {"ok": True, "blocks": [dict(r) for r in rows]}


@router.post("/cards/{card_id}/blocks")
def save_block(request: Request, card_id: str, req: BlockIn):
    if not _card_exists(request, card_id):
        return {"ok": False, "error": "人格卡不存在"}
    title = (req.title or "").strip()
    content = (req.content or "").strip()
    if not title or not content:
        return {"ok": False, "error": "标题和内容不能为空"}
    now = time.time()
    db = _db(request)
    if req.id:
        db.execute(
            "UPDATE persona_raw_blocks SET title=?, kind=?, content=?, sort_order=?, "
            "enabled=?, weight=?, note=?, updated_at=? WHERE id=? AND persona_id=?",
            [title, req.kind or "other", content, int(req.sort_order or 0),
             1 if req.enabled else 0, float(req.weight or 1.0), req.note or "", now,
             int(req.id), card_id],
        )
        return {"ok": True, "id": req.id}
    bid = db.insert(
        "INSERT INTO persona_raw_blocks "
        "(persona_id, title, kind, content, sort_order, enabled, weight, note, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        [card_id, title, req.kind or "other", content, int(req.sort_order or 0),
         1 if req.enabled else 0, float(req.weight or 1.0), req.note or "", now, now],
    )
    return {"ok": True, "id": bid}


@router.delete("/blocks/{block_id}")
def delete_block(request: Request, block_id: int):
    return {"ok": _db(request).execute("DELETE FROM persona_raw_blocks WHERE id = ?", [block_id]) > 0}


@router.get("/wake-words")
def get_wake_words(request: Request, account_id: str = ""):
    card = _active_card(request, account_id)
    if card is None:
        return {"ok": False, "error": "当前没有激活的人格卡", "name": "", "alias": []}
    return {"ok": True, "id": card.id, "name": card.name, "alias": list(card.alias)}


@router.post("/wake-words")
def set_wake_words(req: WakeIn, request: Request, account_id: str = ""):
    card = _active_card(request, account_id)
    if card is None:
        return {"ok": False, "error": "当前没有激活的人格卡"}
    name = req.name.strip()
    if not name:
        return {"ok": False, "error": "主名字不能为空"}
    alias = []
    for a in req.alias:
        a = str(a).strip()
        if a and a != name and a not in alias:
            alias.append(a)
    card.name = name
    card.alias = alias
    PersonaSystem(_db(request), _settings(request)).save_card(card)
    return {"ok": True, "id": card.id, "name": card.name, "alias": alias}
