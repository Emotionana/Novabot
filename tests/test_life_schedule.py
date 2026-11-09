"""一周作息：默认、保存、生效、重置。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.life_service import LifeService


def test_default_week_has_seven_days(db, settings):
    life = LifeService(db, settings)
    data = life.schedule("acc-a")
    assert data["ok"] is True
    assert len(data["days"]) == 7
    for day in data["days"]:
        assert day["slots"]
        assert day["slots"][0]["start"] == "00:00"
        assert day["slots"][-1]["end"] == "24:00"


def test_custom_day_applies_and_reset(db, settings):
    life = LifeService(db, settings)
    data = life.schedule("acc-a")
    slots = data["days"][0]["slots"]
    slots[2]["activity"] = "在测试自定义作息"
    slots[2]["energy"] = 0.99
    life.save_schedule("acc-a", 0, slots)
    got = life.schedule("acc-a")["days"][0]
    assert got["custom"] is True
    assert got["slots"][2]["activity"] == "在测试自定义作息"

    monday = datetime(2026, 11, 9, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    now = life.current("acc-a", now=monday.timestamp())
    assert now.activity == "在测试自定义作息"

    life.reset_schedule("acc-a", 0)
    assert life.schedule("acc-a")["days"][0]["custom"] is False


def test_schedule_rejects_gaps(db, settings):
    life = LifeService(db, settings)
    slots = life.schedule("acc-a")["days"][0]["slots"]
    slots[0]["end"] = "08:00"
    with pytest.raises(ValueError, match="首尾相接"):
        life.save_schedule("acc-a", 0, slots)
