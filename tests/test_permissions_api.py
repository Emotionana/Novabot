"""权限页只显示白名单聊天。"""

from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.web.permissions_api import router


def test_permission_chats_only_whitelisted(db):
    app = FastAPI()
    app.state.db = db
    app.include_router(router)
    client = TestClient(app)
    from app.settings import Settings

    st = Settings(db, account_id="acc-a")
    st.set("security.owner_qq", "999")
    st.set("security.private_whitelist", ["111"])
    st.set("security.group_whitelist", ["222"])
    now = time.time()
    db.execute(
        "INSERT OR IGNORE INTO accounts (id, name, created_at, updated_at) VALUES (?,?,?,?)",
        ["acc-a", "A", now, now],
    )
    for cid, typ in [
        ("private:111", "private"), ("private:333", "private"), ("private:999", "private"),
        ("group:222", "group"), ("group:444", "group"),
    ]:
        db.execute(
            "INSERT OR REPLACE INTO chats (account_id, chat_id, chat_type, title, first_seen, last_seen) VALUES (?,?,?,?,?,?)",
            ["acc-a", cid, typ, cid, now, now],
        )
    data = client.get("/api/permissions", params={"account_id": "acc-a"}).json()
    ids = {c["chat_id"] for c in data["chats"]}
    assert ids == {"private:111", "group:222"}
