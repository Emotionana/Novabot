"""工作台 API：笔记/提醒 CRUD。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.web.tools_api import router


def make_client(db):
    app = FastAPI()
    app.state.db = db
    app.include_router(router)
    return TestClient(app)


def test_note_crud(db, settings):
    settings.set("security.owner_qq", "111")
    client = make_client(db)

    created = client.post("/api/tools/notes", json={"title": "周报", "body": "周五交"}).json()
    assert created["ok"] and created["id"] > 0

    notes = client.get("/api/tools/notes").json()["notes"]
    assert notes[0]["title"] == "周报"

    assert client.delete(f"/api/tools/notes/{created['id']}").json()["ok"] is True
    assert client.get("/api/tools/notes").json()["notes"] == []


def test_reminder_create_and_cancel(db, settings):
    settings.set("security.owner_qq", "111")
    client = make_client(db)

    created = client.post(
        "/api/tools/reminders", json={"text": "喝水", "when": "10分钟后"}
    ).json()
    assert created["ok"], created
    assert created["due"]

    rows = client.get("/api/tools/reminders").json()["reminders"]
    assert rows[0]["status"] == "pending"

    assert client.delete(f"/api/tools/reminders/{created['id']}").json()["ok"] is True
    rows = client.get("/api/tools/reminders").json()["reminders"]
    assert rows[0]["status"] == "cancelled"


def test_note_requires_owner(db):
    client = make_client(db)
    r = client.post("/api/tools/notes", json={"body": "x"}).json()
    assert r["ok"] is False
    assert "主人" in r["error"]


def test_notes_filter_by_account(db, settings):
    settings.set("security.owner_qq", "111")
    client = make_client(db)
    client.post("/api/tools/notes", json={"body": "A 的笔记", "account_id": "a"})
    client.post("/api/tools/notes", json={"body": "B 的笔记", "account_id": "b"})

    rows = client.get("/api/tools/notes?account_id=a").json()["notes"]
    assert [r["body"] for r in rows] == ["A 的笔记"]


def test_note_can_be_edited(db, settings):
    settings.set("security.owner_qq", "111")
    client = make_client(db)
    nid = client.post("/api/tools/notes", json={"body": "旧内容"}).json()["id"]

    r = client.patch(f"/api/tools/notes/{nid}", json={"title": "新标题", "body": "新内容"}).json()
    assert r["ok"] is True
    row = db.query_one("SELECT title, body FROM notes WHERE id = ?", [nid])
    assert row["title"] == "新标题" and row["body"] == "新内容"


def test_reminder_can_be_edited(db, settings):
    settings.set("security.owner_qq", "111")
    client = make_client(db)
    created = client.post(
        "/api/tools/reminders", json={"text": "旧提醒", "when": "10分钟后"}
    ).json()
    assert created["ok"]

    r = client.patch(
        f"/api/tools/reminders/{created['id']}",
        json={"text": "新提醒", "when": "1小时后"},
    ).json()
    assert r["ok"] and r["due"]
    row = db.query_one("SELECT text, status FROM reminders WHERE id = ?", [created["id"]])
    assert row["text"] == "新提醒" and row["status"] == "pending"
