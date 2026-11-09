"""记忆/人格/指代/呼语接线测试。

这些模块以前是"写了没人用"，现在要确认它们真的被主流程调用。
"""

from __future__ import annotations

import pytest

from app.core.attention import Attention
from app.memory.system import MemorySystem
from app.models import ChatType, InboundMessage


def mk(
    text: str,
    *,
    account_id: str = "a",
    chat_id: str | None = None,
    chat_type: ChatType = ChatType.PRIVATE,
    sender: str = "111",
    at: list[str] | None = None,
) -> InboundMessage:
    return InboundMessage(
        account_id=account_id,
        chat_id=chat_id or f"private:{sender}",
        chat_type=chat_type,
        group_id="1" if chat_type is ChatType.GROUP else None,
        sender_qq=sender,
        sender_nick=f"user{sender}",
        text=text,
        at_list=at or [],
    )


def test_emotion_is_saved_only_when_sure(db):
    mem = MemorySystem(db)

    mem.remember(mk("我好开心啊", chat_id="group:1", chat_type=ChatType.GROUP))
    first = db.query("SELECT * FROM emotions")
    assert len(first) == 1
    assert first[0]["confidence"] >= 0.6

    mem.remember(mk("嗯", chat_id="group:1", chat_type=ChatType.GROUP))
    assert len(db.query("SELECT * FROM emotions")) == 1, "中性消息不该刷表"


def test_graph_links_at_but_not_plain_messages(db):
    mem = MemorySystem(db)

    mem.remember(mk("普通一句", chat_id="group:1", chat_type=ChatType.GROUP))
    assert db.scalar("SELECT COUNT(*) FROM edges", default=0) == 0

    mem.remember(mk("叫他一下", chat_id="group:1", chat_type=ChatType.GROUP, at=["222"]))
    assert db.scalar("SELECT COUNT(*) FROM edges", default=0) >= 2
    assert db.scalar("SELECT COUNT(*) FROM nodes", default=0) >= 2


def test_recall_does_not_leak_across_accounts(db):
    mem = MemorySystem(db)
    mem.remember(mk("A 号的专属记忆", account_id="a", chat_id="private:111"))
    mem.remember(mk("B 号的专属记忆", account_id="b", chat_id="private:111"))

    ctx_a = mem.build_context(chat_id="private:111", sender_qq="111", account_id="a")
    ctx_b = mem.build_context(chat_id="private:111", sender_qq="111", account_id="b")

    assert "A 号的专属记忆" in ctx_a and "B 号的专属记忆" not in ctx_a
    assert "B 号的专属记忆" in ctx_b and "A 号的专属记忆" not in ctx_b


def test_profile_is_used_in_context(db):
    mem = MemorySystem(db)
    mem.remember(mk("你好"))
    mem.profiles.set_field("111", "a", "occupation", "程序员")

    ctx = mem.build_context(chat_id="private:111", sender_qq="111", account_id="a")

    assert "程序员" in ctx


@pytest.mark.asyncio
async def test_history_char_limit(db, settings):
    from app.core.pipeline import Pipeline
    from tests.helpers import ScriptedLLM

    mem = MemorySystem(db)
    for i in range(8):
        mem.remember(mk(f"这是第{i}条比较长的历史消息内容"))
    settings.set("llm.history_turns", 10)
    settings.set("llm.history_chars", 100)

    pipe = Pipeline(settings=settings, memory=mem, llm=ScriptedLLM("好"))
    system, messages = await pipe._build_prompt(mk("在吗"))

    total = sum(len(m["content"]) for m in messages)
    assert total <= 100


@pytest.mark.asyncio
async def test_coref_hint_in_group(db, settings):
    from app.core.pipeline import Pipeline
    from tests.helpers import ScriptedLLM

    mem = MemorySystem(db)
    mem.remember(mk("今天好累", chat_id="group:1", chat_type=ChatType.GROUP, sender="111"))
    mem.remember(mk("我觉得还行", chat_id="group:1", chat_type=ChatType.GROUP, sender="222"))
    db.execute("UPDATE people SET name = '小明' WHERE qq = '222'")

    pipe = Pipeline(settings=settings, memory=mem, llm=ScriptedLLM("好"))
    hint = pipe._coref_hint(
        mk("他刚才说的那个呢", chat_id="group:1", chat_type=ChatType.GROUP, sender="111")
    )

    assert "小明" in hint


def test_vocative_name_counts_as_call(settings):
    settings.set("gateway.bot_qq", "999")
    attn = Attention(settings)
    msg = mk("小助手你在吗", chat_id="group:1", chat_type=ChatType.GROUP)

    assert attn._is_called(msg, msg.text, bot_names=["小助手"]) is True
    assert attn._is_called(msg, msg.text, bot_names=[]) is False


def test_system_modules_report(db):
    from app.system_status import collect, summary

    rows = collect(db)
    ids = {r["id"] for r in rows}
    assert {"hippocampus", "cortex", "amygdala", "graph", "profile", "recall",
            "coref", "persona", "llm", "gateway", "proactive", "distill"} <= ids
    assert summary(rows)["missing"] == 0


def test_memory_tier_half_life_params(db, settings):
    from app.memory.cortex import Cortex

    cortex = Cortex(db, settings)
    settings.set("memory.tier1_half_life_days", 365)
    settings.set("memory.tier2_half_life_days", 730)
    assert cortex._half_life_days(1) == 365
    assert cortex._half_life_days(2) == 730
    assert cortex._half_life_days(0) is None
