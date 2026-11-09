"""主动私聊测试：习惯、时间、频控、退避、生成和发送。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.adapters.llm.base import Reply
from app.proactive.habits import (
    habit_fit,
    is_quiet,
    load_habit,
    load_topics,
    response_rate,
)
from app.proactive.service import ProactiveService

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 7, 20, 0, tzinfo=TZ).timestamp()
ACC = "acc"
QQ = "10001"


def at(hour: int, minute: int = 0, day: int = 7) -> float:
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ).timestamp()


def seed_person(
    db,
    *,
    qq: str = QQ,
    account_id: str = ACC,
    n: int = 40,
    days: int = 10,
    last_hours_ago: float = 24,
    owner: bool = False,
    texts: list[str] | None = None,
):
    last = NOW - last_hours_ago * 3600
    first = NOW - days * 86400
    db.execute(
        "INSERT INTO people (qq, account_id, name, msg_count, first_seen, last_seen, is_owner) "
        "VALUES (?,?,?,?,?,?,?)",
        [qq, account_id, "老林", n, first, last, 1 if owner else 0],
    )
    texts = texts or ["今天有点累", "晚饭吃了", "回头聊"]
    for i in range(n):
        ts = last - (i % days) * 86400
        db.execute(
            "INSERT INTO episodes (account_id, chat_id, chat_type, sender_qq, sender_nick, "
            "text, event_at, recv_at) VALUES (?,?,?,?,?,?,?,?)",
            [account_id, f"private:{qq}", "private", qq, "老林", texts[i % len(texts)], ts, ts],
        )


class FakeLLM:
    name = "fake"

    def __init__(self, text: str = "这个点你还没睡？"):
        self.text = text
        self.calls: list[dict] = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return Reply(text=self.text)


class FakeManager:
    def __init__(self, llm: FakeLLM | None = None):
        self.sent: list[tuple[str, str, str]] = []
        self.llm = llm or FakeLLM()
        self.fail = False

    def online_accounts(self):
        return [ACC]

    def llm_for(self, account_id):
        return self.llm

    async def send_private(self, account_id, qq, text):
        if self.fail:
            return False
        self.sent.append((account_id, qq, text))
        return True


def make_service(db, settings, mem, manager=None):
    settings.set("proactive.enabled", True)
    settings.set("proactive.min_messages", 30)
    settings.set("proactive.min_days", 0)
    settings.set("proactive.min_silence_hours", 6.0)
    settings.set("proactive.max_per_day", 1)
    settings.set("proactive.global_max_per_day", 5)
    settings.set("proactive.score_threshold", 0.0)
    settings.set("proactive.quiet_start", 23)
    settings.set("proactive.quiet_end", 8)
    settings.set("proactive.wait_reply_hours", 24)
    settings.set("proactive.interval_hours", 12)
    settings.set("proactive.backoff_base_days", 1)
    settings.set("proactive.max_chars", 60)
    settings.set("proactive.dry_run", False)
    settings.set("proactive.owner_enabled", True)
    settings.set("proactive.owner_max_per_day", 2)
    settings.set("proactive.only_owner", False)

    manager = manager or FakeManager()
    svc = ProactiveService(db, mem, None, settings, manager=manager)
    return svc, manager


@pytest.fixture()
def svc(db, settings, mem):
    return make_service(db, settings, mem)


def test_account_toggle_is_enough_even_if_global_is_off(db, settings, mem):
    settings.set("proactive.enabled", False)
    db.execute(
        "INSERT INTO accounts(id, name, enabled, created_at, updated_at) VALUES (?,?,?,?,?)",
        [ACC, ACC, 1, NOW, NOW],
    )
    from app.settings import Settings

    Settings(db, account_id=ACC).set("proactive.enabled", True)
    svc = ProactiveService(db, mem, None, settings, manager=FakeManager())
    assert svc.enabled() is True
    assert svc.enabled(ACC) is True
    assert svc.status(account_id=ACC)["enabled"] is True


# ---------------------------------------------------------------------------
# 习惯与时间
# ---------------------------------------------------------------------------


def test_habit_learns_evening_window(db):
    seed_person(db)
    profile = load_habit(db, ACC, QQ, now=NOW, tz=TZ, days=30, min_messages=1)

    assert profile is not None
    assert profile.peak_hours[0] == 20
    assert habit_fit(at(20), profile.peak_hours, TZ) > 0.9
    assert habit_fit(at(3), profile.peak_hours, TZ) < 0.3


def test_quiet_hours_wrap_midnight():
    assert is_quiet(at(23, 30), 23, 8, TZ) is True
    assert is_quiet(at(2), 23, 8, TZ) is True
    assert is_quiet(at(9), 23, 8, TZ) is False
    assert is_quiet(at(12), 23, 23, TZ) is False  # start == end 表示关闭


def test_cold_start_needs_enough_messages(db):
    seed_person(db, n=10)
    profile = load_habit(db, ACC, QQ, now=NOW, tz=TZ, days=30, min_messages=30)
    assert profile is None


# ---------------------------------------------------------------------------
# 候选与发送
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_sends_one_and_records(db, settings, mem):
    seed_person(db)
    svc, manager = make_service(db, settings, mem)

    decisions = await svc.tick(now=NOW)

    assert decisions[0]["status"] == "sent"
    assert manager.sent == [(ACC, QQ, "这个点你还没睡？")]
    row = db.query_one("SELECT * FROM proactive_log ORDER BY id DESC LIMIT 1")
    assert row["status"] == "sent"
    assert row["message"] == "这个点你还没睡？"
    assert row["score"] > 0
    state = db.query_one("SELECT * FROM proactive_state WHERE account_id=? AND person_qq=?", [ACC, QQ])
    assert state["daily_count"] == 1
    assert state["last_sent_at"] == pytest.approx(NOW)


@pytest.mark.asyncio
async def test_daily_cap_blocks_second_send(db, settings, mem):
    seed_person(db)
    svc, manager = make_service(db, settings, mem)
    await svc.tick(now=NOW)

    second = await svc.tick(now=NOW + 60)

    assert second[0]["status"] == "skipped"
    assert len(manager.sent) == 1


@pytest.mark.asyncio
async def test_quiet_hours_blocks_tick(db, settings, mem):
    seed_person(db, last_hours_ago=8)
    svc, manager = make_service(db, settings, mem)

    decisions = await svc.tick(now=at(2))

    assert decisions[0]["status"] == "skipped"
    assert "安静" in decisions[0]["reason"]
    assert manager.sent == []


@pytest.mark.asyncio
async def test_recent_activity_blocks(db, settings, mem):
    seed_person(db, last_hours_ago=1)
    svc, manager = make_service(db, settings, mem)

    decisions = await svc.tick(now=NOW)

    assert decisions[0]["status"] == "skipped"
    assert manager.sent == []


@pytest.mark.asyncio
async def test_dry_run_generates_but_does_not_send(db, settings, mem):
    seed_person(db)
    svc, manager = make_service(db, settings, mem)

    decisions = await svc.tick(now=NOW, dry_run=True)

    assert decisions[0]["status"] == "preview"
    assert decisions[0]["message"] == "这个点你还没睡？"
    assert manager.sent == []
    assert db.query_one("SELECT COUNT(*) AS n FROM proactive_log")["n"] == 0


@pytest.mark.asyncio
async def test_threshold_skips_low_score(db, settings, mem):
    seed_person(db)
    svc, manager = make_service(db, settings, mem)
    settings.set("proactive.score_threshold", 0.999)

    decisions = await svc.tick(now=NOW)

    assert decisions[0]["status"] == "skipped"
    assert "门槛" in decisions[0]["reason"]
    assert manager.sent == []


@pytest.mark.asyncio
async def test_send_failure_logs_but_does_not_count(db, settings, mem):
    seed_person(db)
    svc, manager = make_service(db, settings, mem)
    manager.fail = True

    decisions = await svc.tick(now=NOW)

    assert decisions[0]["status"] == "failed"
    row = db.query_one("SELECT * FROM proactive_log ORDER BY id DESC LIMIT 1")
    assert row["status"] == "failed"
    state = db.query_one("SELECT * FROM proactive_state WHERE account_id=? AND person_qq=?", [ACC, QQ])
    assert state is None


# ---------------------------------------------------------------------------
# 反馈与退避
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_reply_marks_replied_and_resets_backoff(db, settings, mem):
    seed_person(db)
    svc, manager = make_service(db, settings, mem)
    await svc.tick(now=NOW)

    ok = svc.on_user_message(ACC, QQ, "刚看到，你怎么也没睡", at=NOW + 300)

    assert ok is True
    row = db.query_one("SELECT * FROM proactive_log ORDER BY id DESC LIMIT 1")
    assert row["status"] == "replied"
    assert row["reply_text"] == "刚看到，你怎么也没睡"
    state = db.query_one("SELECT * FROM proactive_state WHERE account_id=? AND person_qq=?", [ACC, QQ])
    assert state["ignored_streak"] == 0
    assert state["next_eligible_at"] == pytest.approx(NOW + 300 + 12 * 3600)

    # 第二天同一时间可以再发（换一句，避免被去重拦住）
    manager.llm.text = "第二天了，今天忙吗"
    again = await svc.tick(now=NOW + 86400)
    assert again[0]["status"] == "sent"
    assert len(manager.sent) == 2


@pytest.mark.asyncio
async def test_ignored_message_enters_backoff(db, settings, mem):
    seed_person(db)
    svc, manager = make_service(db, settings, mem)
    await svc.tick(now=NOW)

    later = NOW + 25 * 3600
    manager.llm.text = "第二天换个说法"
    decisions = await svc.tick(now=later)

    row = db.query_one("SELECT * FROM proactive_log WHERE status = 'ignored' ORDER BY id DESC LIMIT 1")
    assert row is not None
    state = db.query_one("SELECT * FROM proactive_state WHERE account_id=? AND person_qq=?", [ACC, QQ])
    assert state["ignored_streak"] == 1
    # 软降频：不写死下一次时间，仍可在后续时段发，只降低每天上限
    assert state["next_eligible_at"] is None
    assert decisions[0]["status"] in ("sent", "skipped")


@pytest.mark.asyncio
async def test_preview_shows_candidate_without_sending(db, settings, mem):
    seed_person(db)
    svc, manager = make_service(db, settings, mem)

    out = await svc.preview(now=NOW)

    assert out[0]["status"] == "preview"
    assert out[0]["qq"] == QQ
    assert out[0]["message"]
    assert manager.sent == []


@pytest.mark.asyncio
async def test_fire_sends_manual_message(db, settings, mem):
    seed_person(db)
    svc, manager = make_service(db, settings, mem)

    result = await svc.fire(ACC, QQ, "在忙吗", now=NOW)

    assert result["status"] == "sent"
    assert manager.sent == [(ACC, QQ, "在忙吗")]


# ---------------------------------------------------------------------------
# 生成校验与话题
# ---------------------------------------------------------------------------


def test_clean_message_rejects_probe_and_duplicate(db, settings, mem):
    seed_person(db)
    svc, manager = make_service(db, settings, mem)

    with pytest.raises(RuntimeError):
        svc._clean_message("在吗？", ACC, QQ, settings)

    db.execute(
        "INSERT INTO proactive_log (account_id, person_qq, sent_at, message, status, created_at) "
        "VALUES (?,?,?,?,?,?)",
        [ACC, QQ, NOW - 3600, "上次说的那家店", "sent", NOW - 3600],
    )
    with pytest.raises(RuntimeError):
        svc._clean_message("上次说的那家店", ACC, QQ, settings)


def test_clean_message_truncates_long_text(db, settings, mem):
    seed_person(db)
    svc, manager = make_service(db, settings, mem)
    settings.set("proactive.max_chars", 10)

    text = svc._clean_message("这是一段明显超过十个字的主动消息内容", ACC, QQ, settings)

    assert len(text) <= 10
    assert text.endswith("…")


def test_topics_find_unfinished(db):
    seed_person(db, texts=["你上次说的那个事后来怎么样了？", "好", "嗯"])
    topics = load_topics(db, ACC, QQ, now=NOW)

    assert topics["unfinished"]
    assert "后来" in topics["unfinished"]
    assert topics["recent"]


def test_response_rate_smoothing(db):
    seed_person(db)
    assert response_rate(db, ACC, QQ) == pytest.approx(0.35)

    db.execute(
        "INSERT INTO proactive_log (account_id, person_qq, sent_at, message, status, "
        "replied_at, created_at) VALUES (?,?,?,?,?,?,?)",
        [ACC, QQ, NOW, "x", "replied", NOW, NOW],
    )
    assert response_rate(db, ACC, QQ) > 0.35


def test_group_only_person_is_not_candidate(db, settings, mem):
    """只在群里说过话的人，不主动私聊。"""
    seed_person(db)
    db.execute("UPDATE episodes SET chat_id = 'group:573560374'")
    svc, manager = make_service(db, settings, mem)

    assert svc._collect(ACC, settings, NOW) == []


def test_weekend_and_weekday_habits_differ(db):
    """工作日晚上活跃、周末上午活跃，应该分别算峰值。"""
    db.execute(
        "INSERT INTO people (qq, account_id, name, msg_count, first_seen, last_seen, is_owner) "
        "VALUES (?,?,?,?,?,?,?)",
        [QQ, ACC, "老林", 20, at(20, day=1) - 30 * 86400, at(10, day=8), 0],
    )
    for day in (2, 3, 4, 5, 6):
        for _ in range(3):
            db.execute(
                "INSERT INTO episodes (account_id, chat_id, chat_type, sender_qq, text, event_at, recv_at) "
                "VALUES (?,?,?,?,?,?,?)",
                [ACC, f"private:{QQ}", "private", QQ, "下班了", at(20, day=day), at(20, day=day)],
            )
    for day in (7, 8):
        for _ in range(5):
            db.execute(
                "INSERT INTO episodes (account_id, chat_id, chat_type, sender_qq, text, event_at, recv_at) "
                "VALUES (?,?,?,?,?,?,?)",
                [ACC, f"private:{QQ}", "private", QQ, "周末早", at(10, day=day), at(10, day=day)],
            )

    profile = load_habit(db, ACC, QQ, now=NOW, tz=TZ, days=30, min_messages=1)

    # 2026-11-07 是周六
    sat10 = at(10)
    assert profile.peaks_for(sat10, TZ) == [10]
    assert habit_fit(sat10, profile.peaks_for(sat10, TZ), TZ) > 0.9
    assert habit_fit(at(20), profile.peaks_for(at(20), TZ), TZ) < 0.4


def test_unfinished_ignored_after_bot_replied(db):
    seed_person(db, texts=["明天有空吗？", "嗯", "好"])
    # 最后一条是 bot 接过了 → 不算没聊完
    db.execute(
        "INSERT INTO episodes (account_id, chat_id, chat_type, sender_qq, text, event_at, recv_at) "
        "VALUES (?,?,?,?,?,?,?)",
        [ACC, f"private:{QQ}", "private", "self", "有空呀", NOW - 3600, NOW - 3600],
    )
    topics = load_topics(db, ACC, QQ, now=NOW)
    assert topics["unfinished"] == ""


@pytest.mark.asyncio
async def test_time_slots_allow_one_per_window(db, settings, mem):
    seed_person(db, last_hours_ago=24)
    svc, manager = make_service(db, settings, mem)
    settings.set("proactive.max_per_day", 4)
    settings.set("proactive.global_max_per_day", 10)
    settings.set("proactive.min_silence_hours", 0)
    settings.set("proactive.slot_afternoon", 1)
    settings.set("proactive.slot_evening", 1)

    first = await svc.tick(now=at(15))
    manager.llm.text = "第二个时段的消息"
    second = await svc.tick(now=at(20))
    third = await svc.tick(now=at(20, 30))

    assert first[0]["status"] == "sent"
    assert second[0]["status"] == "sent"
    assert third[0]["status"] == "skipped"
    assert len(manager.sent) == 2


@pytest.mark.asyncio
async def test_slot_can_be_disabled(db, settings, mem):
    seed_person(db, last_hours_ago=24)
    svc, manager = make_service(db, settings, mem)
    settings.set("proactive.slot_evening", 0)

    decisions = await svc.tick(now=at(20))

    assert decisions[0]["status"] == "skipped"
    assert manager.sent == []
