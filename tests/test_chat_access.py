"""聊天白名单和主人称呼。"""

from __future__ import annotations

import asyncio
import time

from app.adapters.llm.base import Capabilities, Reply
from app.core.pipeline import Pipeline
from app.models import ChatType, InboundMessage


class FakeLLM:
    name = "fake"

    def __init__(self):
        self.calls: list[str] = []

    async def chat(self, system="", messages=None, **kw) -> Reply:
        self.calls.append(system)
        return Reply(text="好")

    def capabilities(self) -> Capabilities:
        return Capabilities(tools=False, images=False, streaming=False, max_context=8192)


def msg(*, text="在吗", sender="30003", private=True, group_id="1"):
    return InboundMessage(
        account_id="default",
        chat_id=f"private:{sender}" if private else f"group:{group_id}",
        chat_type=ChatType.PRIVATE if private else ChatType.GROUP,
        group_id=None if private else group_id,
        group_name=None if private else "测试群",
        sender_qq=sender,
        sender_nick="测试",
        text=text,
        event_at=time.time(),
    )


def pipe(settings, mem):
    settings.set("security.owner_qq", "10001")
    settings.set("reply.private_always", True)
    settings.set("reply.min_gap_sec", 0)
    settings.set("reply.cooldown_sec", 0)
    settings.set("reply.max_per_min", 0)
    settings.set("llm.history_turns", 0)
    llm = FakeLLM()
    return Pipeline(settings=settings, memory=mem, llm=llm, owner_qq="10001"), llm


def test_private_whitelist_owner_and_blocked(settings, mem):
    settings.set("chat.private_access", "whitelist")
    settings.set("security.private_whitelist", ["20002"])
    p, llm = pipe(settings, mem)

    blocked = asyncio.run(p.handle(msg(sender="30003")))
    assert blocked.error == "不在聊天白名单"
    assert not llm.calls
    assert mem.db.scalar("SELECT COUNT(*) FROM episodes", default=0) == 0

    allowed = asyncio.run(p.handle(msg(sender="20002")))
    assert allowed.error is None
    assert llm.calls

    owner = asyncio.run(p.handle(msg(sender="10001")))
    assert owner.error is None


def test_group_whitelist_and_off(settings, mem):
    settings.set("chat.group_access", "whitelist")
    settings.set("security.group_whitelist", ["2", "group:3"])
    p, _ = pipe(settings, mem)
    assert p._chat_allowed(msg(private=False, group_id="1")) is False
    assert p._chat_allowed(msg(private=False, group_id="2")) is True
    assert p._chat_allowed(msg(private=False, group_id="3")) is True

    settings.set("chat.group_access", "off")
    assert p._chat_allowed(msg(private=False, group_id="2")) is False


def test_owner_title_in_prompt(settings, mem):
    settings.set("chat.owner_title", "老板")
    settings.set("chat.owner_aliases", ["哥哥"])
    p, llm = pipe(settings, mem)
    r = asyncio.run(p.handle(msg(sender="10001")))
    assert r.error is None
    assert "老板" in llm.calls[0]
    assert "哥哥" in llm.calls[0]
