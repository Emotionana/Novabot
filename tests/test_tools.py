"""工具调用 / 笔记 / 提醒测试。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.adapters.llm.base import Capabilities, Reply, ToolCall
from app.core.pipeline import Pipeline
from app.tools import REGISTRY, ToolContext
from app.tools.reminders import ReminderService
from app.tools.when import parse_when
from tests.helpers import ScriptedLLM, group_msg, private_msg

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 7, 20, 0, tzinfo=TZ).timestamp()


def ctx(db, *, chat_id="private:111", is_owner=True, now=NOW) -> ToolContext:
    return ToolContext(
        db=db, account_id="a", chat_id=chat_id, person_qq="111",
        is_owner=is_owner, now=now, tz=TZ,
    )


# ---------------------------------------------------------------------------
# 时间解析
# ---------------------------------------------------------------------------


def test_parse_when_forms():
    assert parse_when("10分钟后", now=NOW, tz=TZ) == NOW + 600
    assert parse_when("2小时后", now=NOW, tz=TZ) == NOW + 7200

    tomorrow = parse_when("明天 09:30", now=NOW, tz=TZ)
    assert datetime.fromtimestamp(tomorrow, TZ).strftime("%m-%d %H:%M") == "11-08 09:30"

    later = parse_when("21:15", now=NOW, tz=TZ)
    assert datetime.fromtimestamp(later, TZ).strftime("%m-%d %H:%M") == "11-07 21:15"

    next_day = parse_when("09:00", now=NOW, tz=TZ)
    assert datetime.fromtimestamp(next_day, TZ).strftime("%m-%d %H:%M") == "11-08 09:00"

    with pytest.raises(ValueError):
        parse_when("下周三下午", now=NOW, tz=TZ)


# ---------------------------------------------------------------------------
# 工具权限与审计
# ---------------------------------------------------------------------------


def test_notes_tool_is_owner_private_only(db):
    ok = REGISTRY.execute("add_note", {"body": "买咖啡豆"}, ctx(db))
    assert "记下了" in ok
    assert db.scalar("SELECT COUNT(*) FROM notes", default=0) == 1

    denied = REGISTRY.execute(
        "add_note", {"body": "群里写笔记"}, ctx(db, chat_id="group:1", is_owner=True)
    )
    assert "私聊" in denied
    assert db.scalar("SELECT COUNT(*) FROM notes", default=0) == 1

    stranger = REGISTRY.execute(
        "add_note", {"body": "陌生人写"}, ctx(db, is_owner=False)
    )
    assert "主人" in stranger


def test_tool_call_is_audited(db):
    REGISTRY.execute("current_time", {}, ctx(db))

    row = db.query_one("SELECT * FROM tool_calls ORDER BY id DESC LIMIT 1")
    assert row["tool"] == "current_time"
    assert row["ok"] == 1
    assert row["account_id"] == "a"


def test_add_and_list_reminder(db):
    out = REGISTRY.execute(
        "add_reminder", {"text": "喝水", "when": "10分钟后"}, ctx(db)
    )
    assert "喝水" in out

    listed = REGISTRY.execute("list_reminders", {}, ctx(db))
    assert "喝水" in listed

    row = db.query_one("SELECT * FROM reminders")
    assert row["status"] == "pending"
    assert row["due_at"] == pytest.approx(NOW + 600)


# ---------------------------------------------------------------------------
# Pipeline 工具循环
# ---------------------------------------------------------------------------


class ToolLLM:
    name = "tool-fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def capabilities(self):
        return Capabilities(tools=True)

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self.replies.pop(0)


def prep(settings):
    settings.set("tools.enabled", True)
    settings.set("tools.max_per_call", 5)
    settings.set("security.owner_qq", "222")
    settings.set("reply.cooldown_sec", 0)
    settings.set("reply.max_per_min", 0)
    settings.set("gateway.bot_qq", "999")


@pytest.mark.asyncio
async def test_pipeline_runs_tool_then_replies(db, mem, settings):
    prep(settings)
    llm = ToolLLM(
        [
            Reply(text="", tool_calls=[ToolCall(id="c1", name="add_note", arguments={"body": "周五交周报"})]),
            Reply(text="记好了，周五提醒你"),
        ]
    )
    pipe = Pipeline(settings=settings, memory=mem, llm=llm)

    result = await pipe.handle(private_msg("记一下：周五交周报"))

    assert result.replied is True
    assert result.text == "记好了，周五提醒你"
    assert db.scalar("SELECT COUNT(*) FROM notes", default=0) == 1
    assert llm.calls[0]["tools"], "第一次调用应该带 tools"
    assert any(m.get("role") == "tool" for m in llm.calls[1]["messages"])


@pytest.mark.asyncio
async def test_pipeline_no_tools_when_disabled(db, mem, settings):
    settings.set("tools.enabled", False)
    llm = ScriptedLLM("好")
    pipe = Pipeline(settings=settings, memory=mem, llm=llm)

    await pipe.handle(private_msg("在吗"))

    assert not llm.calls[0].get("tools")


@pytest.mark.asyncio
async def test_tool_write_rejected_in_group(db, mem, settings):
    prep(settings)
    llm = ToolLLM(
        [
            Reply(text="", tool_calls=[ToolCall(id="c1", name="add_note", arguments={"body": "x"})]),
            Reply(text="群里不能记"),
        ]
    )
    pipe = Pipeline(settings=settings, memory=mem, llm=llm)

    result = await pipe.handle(group_msg("帮我记一下", at=["999"]))

    assert result.replied
    assert db.scalar("SELECT COUNT(*) FROM notes", default=0) == 0
    audit = db.query_one("SELECT * FROM tool_calls ORDER BY id DESC LIMIT 1")
    assert audit["ok"] == 0


# ---------------------------------------------------------------------------
# 提醒服务
# ---------------------------------------------------------------------------


class FakeManager:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent: list[tuple[str, str, str]] = []

    async def send_to_chat(self, acc_id, chat_id, text):
        self.sent.append((acc_id, chat_id, text))
        return self.ok


@pytest.mark.asyncio
async def test_reminder_service_sends_due(db):
    db.execute(
        "INSERT INTO reminders (account_id, person_qq, chat_id, text, due_at, status, attempts, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ["a", "111", "private:111", "喝水", NOW - 10, "pending", 0, NOW - 100],
    )
    manager = FakeManager()
    svc = ReminderService(db, manager)

    out = await svc.check_due(now=NOW)

    assert out[0]["status"] == "sent"
    assert manager.sent == [("a", "private:111", "⏰ 提醒：喝水")]
    assert db.query_one("SELECT status FROM reminders")["status"] == "sent"


@pytest.mark.asyncio
async def test_reminder_retries_then_fails(db):
    db.execute(
        "INSERT INTO reminders (account_id, person_qq, chat_id, text, due_at, status, attempts, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ["a", "111", "private:111", "喝水", NOW - 10, "pending", 2, NOW - 100],
    )
    svc = ReminderService(db, FakeManager(ok=False))

    out = await svc.check_due(now=NOW)

    assert out[0]["status"] == "failed"
    assert db.query_one("SELECT status FROM reminders")["status"] == "failed"
