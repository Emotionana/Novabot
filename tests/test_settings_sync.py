"""设置同步：只同步通用行为，不同步 TTS/接口/端口/凭据。"""

from __future__ import annotations

import time

from app.settings import Settings
from app.settings_sync import available_groups, sync_settings


def _account(db, acc_id: str):
    now = time.time()
    db.execute(
        "INSERT INTO accounts(id, name, enabled, created_at, updated_at) VALUES (?,?,?,?,?)",
        [acc_id, acc_id, 1, now, now],
    )


def test_sync_safe_groups_and_skip_endpoints(db):
    _account(db, "acc-a")
    _account(db, "acc-b")
    a = Settings(db, account_id="acc-a")
    b = Settings(db, account_id="acc-b")
    a.set("reply.max_per_min", 7)
    b.set("reply.max_per_min", 3)
    a.set("media.vision_base_url", "http://tts-like-endpoint")
    b.set("media.vision_base_url", "http://own-endpoint")

    r = sync_settings(db, "acc-a", ["回复", "媒体"])
    assert r["ok"] is True
    assert "media.vision_base_url" not in r["keys"]
    assert b.get("reply.max_per_min") == 7
    assert b.get("media.vision_base_url") == "http://own-endpoint"


def test_tts_and_gateway_not_syncable():
    groups = available_groups()
    assert "语音" not in groups
    assert "接入" not in groups
    assert "模型" not in groups
    assert "平台" not in groups


def test_sync_only_selected_targets(db):
    _account(db, "acc-a")
    _account(db, "acc-b")
    _account(db, "acc-c")
    a = Settings(db, account_id="acc-a")
    b = Settings(db, account_id="acc-b")
    c = Settings(db, account_id="acc-c")
    a.set("reply.max_per_min", 9)

    r = sync_settings(db, "acc-a", ["回复"], ["acc-b"])
    assert r["ok"] is True
    assert [x["id"] for x in r["targets"]] == ["acc-b"]
    assert b.get("reply.max_per_min") == 9
    assert c.get("reply.max_per_min") != 9
