"""防抖：等对方把话说完，只回最后一条候选消息。"""

from __future__ import annotations

import asyncio
import time

from app.core.pipeline import Pipeline
from app.models import ChatType, InboundMessage


class FakeLLM:
    name = "fake"

    def __init__(self):
        self.calls = 0

    async def chat(self, system="", messages=None, **kw):
        from app.adapters.llm.base import Reply

        self.calls += 1
        return Reply(text="好")

    def capabilities(self):
        from app.adapters.llm.base import Capabilities

        return Capabilities(tools=False, images=False, streaming=False, max_context=8192)


def _msg(text: str, at: float) -> InboundMessage:
    return InboundMessage(
        account_id="a1", chat_id="group:1", chat_type=ChatType.GROUP,
        group_id="1", sender_qq="20002", sender_nick="测试", text=text, event_at=at,
    )


def test_debounce_only_replies_to_last_candidate(mem, settings):
    settings.set("reply.group_mode", "always")
    settings.set("reply.cooldown_sec", 0.2)
    settings.set("reply.group_rate_max_msgs", 0)
    llm = FakeLLM()
    p = Pipeline(settings=settings, memory=mem, llm=llm, owner_qq="10001")
    now = time.time()

    async def run():
        return await asyncio.gather(
            p.handle(_msg("第一句", now)),
            p.handle(_msg("第二句", now + 0.01)),
        )

    first, second = asyncio.run(run())
    assert first.replied is False
    assert second.replied is True
    assert llm.calls == 1
