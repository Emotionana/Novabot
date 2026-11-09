"""记忆编辑/删除：必须有编辑保险，且只能动当前账号。"""

from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.web.data_api import router


def _client(db):
    app = FastAPI()
    app.state.db = db
    app.include_router(router)
    return TestClient(app)


def _seed(db):
    now = time.time()
    db.execute(
        "INSERT OR IGNORE INTO accounts (id, name, created_at, updated_at) VALUES (?,?,?,?)",
        ["acc-a", "A", now, now],
    )
    db.execute(
        "INSERT INTO episodes (account_id, chat_id, sender_qq, text, event_at, recv_at) VALUES (?,?,?,?,?,?)",
        ["acc-a", "private:1", "1", "旧对话", now, now],
    )
    db.execute(
        "INSERT INTO facts (account_id, subject_id, text, recorded_at) VALUES (?,?,?,?)",
        ["acc-a", "1", "旧事实", now],
    )
    db.execute(
        "INSERT INTO gists (account_id, period_from, period_to, text, created_at) VALUES (?,?,?,?,?)",
        ["acc-a", now - 10, now, "旧摘要", now],
    )
    return {
        "episode_id": db.scalar("SELECT id FROM episodes WHERE account_id='acc-a' LIMIT 1"),
        "fact_id": db.scalar("SELECT id FROM facts WHERE account_id='acc-a' LIMIT 1"),
        "gist_id": db.scalar("SELECT id FROM gists WHERE account_id='acc-a' LIMIT 1"),
    }


def test_edit_requires_safety_switch(db):
    ids = _seed(db)
    c = _client(db)
    r = c.patch(f"/api/data/facts/{ids['fact_id']}", json={"text": "新事实", "account_id": "acc-a"})
    assert r.status_code == 403


def test_edit_and_soft_delete_by_account(db):
    ids = _seed(db)
    c = _client(db)
    h = {"X-Novabot-Edit": "1"}

    r = c.patch(f"/api/data/facts/{ids['fact_id']}", json={"text": "新事实", "account_id": "acc-a"}, headers=h)
    assert r.status_code == 200
    assert db.scalar("SELECT text FROM facts WHERE id=?", [ids["fact_id"]]) == "新事实"

    r = c.delete(f"/api/data/gists/{ids['gist_id']}", params={"account_id": "acc-a"}, headers=h)
    assert r.status_code == 200
    assert db.scalar("SELECT gone FROM gists WHERE id=?", [ids["gist_id"]]) == 1

    r = c.patch(f"/api/data/episodes/{ids['episode_id']}", json={"text": "改对话", "account_id": "acc-b"}, headers=h)
    assert r.status_code == 404
