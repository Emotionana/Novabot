"""回复落库：多轮对话的前提。"""

from __future__ import annotations

import pytest

from app.core.pipeline import Pipeline
from tests.helpers import ScriptedLLM, group_msg, private_msg


@pytest.mark.asyncio
async def test_bot_reply_is_stored(settings, mem):
    settings.set("reply.max_per_min", 0)
    settings.set("reply.cooldown_sec", 0)
    llm = ScriptedLLM("在的")
    pipe = Pipeline(settings=settings, memory=mem, llm=llm)

    result = await pipe.handle(private_msg("在吗"))

    assert result.replied
    assert result.bot_episode_id

    rows = mem.hippocampus.recent("private:222")
    assert [e.sender_qq for e in rows] == ["222", "self"]
    assert rows[-1].text == "在的"

    # 不能凭空长出一个叫 self 的人
    assert mem.db.query_one("SELECT 1 FROM people WHERE qq = 'self'") is None


@pytest.mark.asyncio
async def test_history_contains_assistant_and_no_duplicate(settings, mem):
    settings.set("reply.max_per_min", 0)
    settings.set("reply.cooldown_sec", 0)
    llm = ScriptedLLM("第一句", "第二句")
    pipe = Pipeline(settings=settings, memory=mem, llm=llm)

    await pipe.handle(private_msg("你好"))
    await pipe.handle(private_msg("刚才说啥"))

    messages = llm.calls[-1]["messages"]
    roles = [m["role"] for m in messages]

    assert roles[-1] == "user", "当前这条要在最后"
    assert roles.count("user") == 2, f"当前消息不该重复出现：{roles}"
    assert "assistant" in roles, f"历史里应该有自己说过的话：{roles}"


@pytest.mark.asyncio
async def test_reply_to_bot_counts_as_called(settings, mem):
    settings.set("gateway.bot_qq", "10002")
    settings.set("reply.group_mode", "mention")
    settings.set("reply.max_per_min", 0)
    settings.set("reply.cooldown_sec", 0)
    llm = ScriptedLLM("嗯", "嗯")
    pipe = Pipeline(settings=settings, memory=mem, llm=llm)

    first = await pipe.handle(group_msg("小助手你好", at=["10002"]))
    assert first.replied
    bot_ep = first.bot_episode_id
    assert pipe._last_bot_reply["group:1"] == bot_ep

    # 回复 bot 那条消息，即使没 @ 也该算叫我
    second = await pipe.handle(group_msg("对啊", reply_to=bot_ep))
    assert second.replied, f"应该认出在回复我：{second.decision.reason}"
