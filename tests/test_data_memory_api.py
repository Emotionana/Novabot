"""记忆分区接口：概览 / 分区 / 搜索。"""

from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.web.data_api import router


def make_client(db):
    app = FastAPI()
    app.state.db = db
    app.include_router(router)
    return TestClient(app)


def seed(db):
    now = time.time()
    db.execute(
        "INSERT INTO accounts (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ["a1", "测试", now, now],
    )
    db.execute(
        "INSERT INTO episodes (account_id, chat_id, sender_qq, sender_nick, text, event_at, recv_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["a1", "private:1", "1001", "主人", "今天在西湖边看到一只橘猫", now, now],
    )
    db.execute(
        "INSERT INTO facts (account_id, subject_type, subject_id, text, tier, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["a1", "person", "1001", "主人喜欢猫，尤其是橘猫", 0, now],
    )
    db.execute(
        "INSERT INTO gists (account_id, level, chat_id, sender_qq, period_from, period_to, text, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ["a1", 1, "private:1", "1001", now - 3600, now, "这周聊了杭州、猫和火锅", now],
    )
    db.execute(
        "INSERT INTO people (qq, account_id, name, alias, first_seen, last_seen, msg_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["1001", "a1", "主人", "猫奴", now, now, 12],
    )
    db.execute(
        "INSERT INTO people (qq, account_id, name, alias, first_seen, last_seen, msg_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["2002", "a1", "阿岚", "", now - 86400, now - 3600, 88],
    )
    db.execute(
        "INSERT INTO documents (account_id, title, source, text, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["a1", "杭州旅行笔记", "demo.md", "西湖边有很多猫", now, now],
    )


def test_overview_and_sections(db):
    seed(db)
    c = make_client(db)
    ov = c.get("/api/data/overview?account_id=a1").json()
    assert ov["counts"]["episodes"] == 1
    assert ov["counts"]["facts"] == 1
    assert ov["counts"]["gists"] == 1
    assert ov["counts"]["people"] == 2
    assert ov["counts"]["documents"] == 1
    assert ov["recent_episodes"][0]["sender_nick"] == "主人"

    assert c.get("/api/data/gists?account_id=a1").json()["gists"][0]["level"] == 1
    assert c.get("/api/data/episodes?account_id=a1&q=橘猫").json()["episodes"][0]["text"].startswith("今天")


def test_search_across_sections(db):
    seed(db)
    c = make_client(db)
    r = c.get("/api/data/search?q=猫&account_id=a1").json()
    assert r["total"] >= 4
    assert any("橘猫" in e["text"] for e in r["episodes"])
    assert any("猫" in f["text"] for f in r["facts"])
    assert any("猫" in d["title"] or "猫" in (d.get("source") or "") for d in r["documents"]) or r["documents"]
    empty = c.get("/api/data/search?q=&account_id=a1").json()
    assert empty["total"] == 0


def test_people_sort_and_total(db):
    seed(db)
    c = make_client(db)
    r = c.get("/api/data/people?account_id=a1&sort=messages&limit=1").json()
    assert r["total"] == 2
    assert len(r["people"]) == 1
    assert r["people"][0]["name"] == "阿岚"
    assert r["people"][0]["avatar"].startswith("https://q1.qlogo.cn/")
    by_name = c.get("/api/data/people?account_id=a1&sort=name").json()["people"]
    assert [p["name"] for p in by_name] == ["主人", "阿岚"]
