"""表情库：每账号隔离、去重、scope、编辑。"""

from __future__ import annotations

import asyncio
import base64
import struct
import time

from app.actions import ActionRouter
from app.media_service import MediaService
from app.models import ChatType, InboundMessage
from app.settings import Settings
from app.stickers import StickerService


def _png(w=100, h=100) -> bytes:
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" +
            struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00" + b"\x00" * 20)


def _svc(db, account="acc-a"):
    st = Settings(db, account_id=account)
    st.set("stickers.enabled", True)
    st.set("stickers.collect", False)
    return StickerService(db, st, account), st


def _msg(chat_id="group:1", *, private=False, sender="20002"):
    return InboundMessage(account_id="acc-a", chat_id=chat_id, chat_type=ChatType.PRIVATE if private else ChatType.GROUP,
                          sender_qq=sender, text="x", event_at=time.time())


def test_library_is_per_account_and_dedupes(db, settings):
    a, _ = _svc(db, "acc-a")
    b, _ = _svc(db, "acc-b")
    r = a.add_bytes(_png(), name="开心", tags=["开心"], scope="group_safe")
    assert r["ok"] and not r.get("existed")
    assert a.list()[0]["name"] == "开心"
    assert b.list() == []
    assert a.add_bytes(_png(), name="重复")["existed"] is True
    assert len(a.list()) == 1


def test_owner_only_not_available_in_group(db, settings):
    svc, _ = _svc(db)
    svc.add_bytes(_png(), name="私聊", tags=["开心"], scope="owner_only")
    assert svc.pick("开心", _msg(sender="99999"), owner_qq="10001") is None
    assert svc.pick("开心", _msg("private:10001", private=True, sender="10001"), owner_qq="10001") is not None


def test_update_and_delete(db, settings):
    svc, _ = _svc(db)
    item = svc.add_bytes(_png(), name="旧", tags=["开心"])["item"]
    assert svc.update(item["md5"], name="新", tags=["无语"], scope="group_safe")["ok"] is True
    assert svc.list()[0]["name"] == "新"
    assert svc.delete(item["md5"])["ok"] is True
    assert svc.list() == []


def test_natural_language_sticker(db, settings):
    settings.set("security.owner_qq", "10001")
    svc, st = _svc(db, "acc-a")
    item = svc.add_bytes(_png(), name="开心", tags=["开心"], scope="owner_only")["item"]
    router = ActionRouter(db, st, None, account_id="acc-a")
    m = _msg("private:10001", private=True, sender="10001")
    m.text = "发个开心的表情包"
    r = asyncio.run(router.handle(m, is_owner=True))
    assert r is not None
    assert r.sticker == item["md5"]


def test_collect_sticker_from_chat(db, settings):
    svc, st = _svc(db)
    st.set("stickers.collect", True)
    st.set("security.owner_qq", "10001")
    m = _msg("private:10001", private=True, sender="10001")
    m.media = [__import__("app.models", fromlist=["MediaItem"]).MediaItem(
        kind="sticker", url="base64://" + base64.b64encode(_png()).decode(),
    )]
    assert asyncio.run(svc.collect(m, allowed=True)) == 1
    assert svc.list()[0]["scope"] == "owner_only"
    assert svc.list()[0]["source"] == "chat"


def test_semantic_pick_requires_meaning(db, settings):
    settings.set("security.owner_qq", "10001")
    svc, st = _svc(db)
    svc.add_bytes(_png(), name="开心", tags=["开心"], scope="owner_only")
    m = _msg("private:10001", private=True, sender="10001")
    assert svc.pick("今天很开心", m, owner_qq="10001") is not None
    assert svc.pick("今天很生气", m, owner_qq="10001") is None
    st.set("stickers.auto_private", 100)
    st.set("stickers.cooldown_sec", 0)
    # 语义匹配不到时，按概率从可用库里随机补一张，否则这个概率形同虚设
    assert svc.auto_pick("今天很生气", m, owner_qq="10001") is not None


def test_analyze_stores_semantics(db, settings, monkeypatch):
    svc, _ = _svc(db)
    item = svc.add_bytes(_png(), name="表情", scope="owner_only")["item"]

    async def fake(self, data, mime, prompt, *, max_tokens=500):
        return '{"description":"一只猫在笑","text":"","emotion":"开心","meaning":"开心地回应","use_when":"对方说好事时","avoid_when":"严肃工作","tags":["猫猫","笑"]}'

    monkeypatch.setattr(MediaService, "vision_text", fake)
    r = asyncio.run(svc.analyze(item["md5"], llm=object()))
    assert r["ok"] is True
    got = svc.list()[0]
    assert got["emotion"] == "开心"
    assert "开心" in got["tags"]
    assert got["analyzed"] is True
    assert "猫猫" in got["tags"] and "笑" in got["tags"]
