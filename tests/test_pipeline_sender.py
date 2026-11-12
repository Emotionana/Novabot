"""Prompt 必须带真实发送者和 QQ，避免把群成员认成主人。"""

from __future__ import annotations

import asyncio
import time

from app.adapters.llm.base import Capabilities, Reply
from app.core.pipeline import Pipeline
from app.models import ChatType, InboundMessage


class FakeLLM:
    name = "fake"

    async def chat(self, system="", messages=None, **kw):
        return Reply(text="嗯")

    def capabilities(self):
        return Capabilities(tools=False, images=False, streaming=False, max_context=8192)


def _msg(*, sender="20001", nick="测试用户", text="😱😱😱", private=False):
    return InboundMessage(
        account_id="default",
        chat_id="private:10001" if private else "group:100001",
        chat_type=ChatType.PRIVATE if private else ChatType.GROUP,
        group_id=None if private else "100001",
        group_name=None if private else "测试群",
        sender_qq=sender,
        sender_nick=nick,
        text=text,
        event_at=time.time(),
    )


def test_group_prompt_carries_real_sender_qq(db, settings):
    settings.set("security.owner_qq", "10001")
    settings.set("gateway.bot_qq", "10002")
    pipe = Pipeline(settings=settings, memory=None, llm=FakeLLM(), owner_qq="10001")

    system, messages = asyncio.run(pipe._build_prompt(_msg(), is_owner=False))
    assert "QQ:20001" in system
    assert "不是主人" in system
    assert messages[-1]["content"] == "测试用户（QQ:20001）：😱😱😱"


def test_private_prompt_carries_real_sender_qq(db, settings):
    settings.set("security.owner_qq", "10001")
    settings.set("gateway.bot_qq", "10002")
    pipe = Pipeline(settings=settings, memory=None, llm=FakeLLM(), owner_qq="10001")

    _system, messages = asyncio.run(
        pipe._build_prompt(_msg(sender="10001", nick="测试用户", text="在吗", private=True), is_owner=True)
    )
    assert messages[-1]["content"] == "测试用户（QQ:10001）：在吗"
