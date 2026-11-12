"""人格卡 CRUD。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.web.persona_api import router


def make_client(db):
    app = FastAPI()
    app.state.db = db
    app.include_router(router)
    return TestClient(app)


def test_persona_card_crud_and_activate(db):
    client = make_client(db)
    r = client.post("/api/persona/cards", json={
        "id": "demo", "name": "小助手",
        "alias": ["演示设定"],
        "identity": "提瓦特的演示设定",
        "background": "冷静、傲慢、偶尔心软",
        "traits": [{"name": "傲娇", "weight": 0.8, "trigger": "被夸"}],
        "speech": {"length": "short", "sass": 0.7, "habits": ["哼"]},
        "boundaries": ["不承认自己是 AI"],
        "examples": [{"user": "你好", "assistant": "哼，有事？"}],
        "tags": ["原神"],
    }).json()
    assert r["ok"], r

    cards = client.get("/api/persona/cards").json()
    assert cards["cards"][0]["name"] == "小助手"
    assert cards["current"] == "demo"

    full = client.get("/api/persona/cards/demo").json()["card"]
    assert full["speech"]["sass"] == 0.7
    assert full["traits"][0]["name"] == "傲娇"

    assert client.post("/api/persona/cards/demo/activate").json()["ok"] is True
    assert client.delete("/api/persona/cards/demo").json()["ok"] is True
    assert client.get("/api/persona/cards").json()["cards"] == []


def test_persona_keywords_and_blocks(db):
    client = make_client(db)
    client.post("/api/persona/cards", json={"id": "p1", "name": "P1", "activate": False})

    r = client.post("/api/persona/cards/p1/keywords", json={
        "keywords": ["好看"], "match_mode": "any",
        "effects": [{"target": "trait.傲娇", "delta": -0.4, "duration": 300}],
        "priority": 10, "note": "被夸",
    }).json()
    assert r["ok"], r
    rules = client.get("/api/persona/cards/p1/keywords").json()["rules"]
    assert rules[0]["keywords"] == ["好看"]

    b = client.post("/api/persona/cards/p1/blocks", json={
        "title": "说话方式", "kind": "style", "content": "短句，少用句号",
    }).json()
    assert b["ok"] and b["id"]
    blocks = client.get("/api/persona/cards/p1/blocks").json()["blocks"]
    assert blocks[0]["title"] == "说话方式"

    assert client.delete(f"/api/persona/keywords/{rules[0]['id']}").json()["ok"] is True
    assert client.delete(f"/api/persona/blocks/{b['id']}").json()["ok"] is True


def test_persona_avatar_upload_and_read(db):
    import struct

    client = make_client(db)
    client.post("/api/persona/cards", json={"id": "p-avatar", "name": "头像测试", "activate": False})
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 100, 100) + b"\x08\x06\x00\x00\x00" + b"\x00" * 20
    r = client.post("/api/persona/cards/p-avatar/avatar", files={"file": ("a.png", png, "image/png")}).json()
    assert r["ok"], r
    assert client.get("/api/persona/cards/p-avatar").json()["card"]["avatar"] == r["avatar"]
    img = client.get("/api/persona/cards/p-avatar/avatar")
    assert img.status_code == 200
    assert img.headers["content-type"].startswith("image/")
