"""生活模块：作息、持久化和上下文。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.life_service import LifeService

TZ = ZoneInfo("Asia/Shanghai")


def at(hour: int, minute: int = 0, day: int = 9) -> float:
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ).timestamp()


def test_life_phase_and_persistence(db, settings):
    life = LifeService(db, settings)
    first = life.current("acc", at(9))
    second = life.current("acc", at(9, 30))
    assert first.phase == "morning"
    assert first.can_chat is True
    assert first.activity == second.activity

    night = life.current("acc", at(2))
    assert night.phase == "sleep"
    assert night.can_chat is False


def test_life_plan_is_stored_once(db, settings):

    life = LifeService(db, settings)
    life.current("acc", at(10))
    row = db.query_one("SELECT plan_json FROM life_days WHERE account_id='acc'")
    assert row and '"weekend": false' in row["plan_json"]
    count = db.query_one("SELECT COUNT(*) AS n FROM life_days WHERE account_id='acc'")["n"]
    assert count == 1


def test_life_prompt_and_status(db, settings):
    life = LifeService(db, settings)
    text = life.prompt_context("acc", at(20))
    assert "此刻生活" in text
    assert "精力" in text

    status = life.status("acc", at(20))
    assert status["ok"] is True
    assert status["now"]["phase"] == "leisure"
    assert status["phases"]
