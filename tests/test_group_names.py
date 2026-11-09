"""群名片不能覆盖 QQ 昵称：people.name 是本名，群名片单独存。"""

from __future__ import annotations

import time

from app.models import ChatType, InboundMessage


def _group_msg(nick: str, card: str, at: float) -> InboundMessage:
    return InboundMessage(
        account_id="a1", chat_id="group:999", chat_type=ChatType.GROUP,
        group_id="999", sender_qq="12345", sender_nick=nick, sender_card=card,
        text="hi", event_at=at, recv_at=at,
    )


def test_group_card_does_not_overwrite_qq_name(mem, db):
    now = time.time()
    mem.hippocampus.write(_group_msg("本名", "群里的名片", now))
    row = db.query_one("SELECT name FROM people WHERE qq='12345' AND account_id='a1'")
    assert row["name"] == "本名"
    g = db.query_one("SELECT card FROM person_group_names WHERE qq='12345' AND account_id='a1' AND group_id='999'")
    assert g["card"] == "群里的名片"

    # 换群名片，名字仍是 QQ 昵称
    mem.hippocampus.write(_group_msg("本名", "新群名片", now + 10))
    row = db.query_one("SELECT name FROM people WHERE qq='12345' AND account_id='a1'")
    assert row["name"] == "本名"
    g = db.query_one("SELECT card FROM person_group_names WHERE qq='12345' AND account_id='a1' AND group_id='999'")
    assert g["card"] == "新群名片"


def test_group_names_in_person_detail(mem, db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.web.data_api import router

    mem.hippocampus.write(_group_msg("本名", "群里的名片", time.time()))
    app = FastAPI()
    app.state.db = db
    app.include_router(router)
    c = TestClient(app)
    r = c.get("/api/data/person/12345?account_id=a1").json()
    assert r["ok"] is True
    assert r["person"]["name"] == "本名"
    assert r["group_names"][0]["card"] == "群里的名片"
    assert r["group_names"][0]["group_id"] == "999"
