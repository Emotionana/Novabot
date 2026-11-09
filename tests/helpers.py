"""测试用的小工具。"""

from __future__ import annotations

import time

from app.adapters.llm.base import Reply
from app.models import ChatType, InboundMessage


class ScriptedLLM:
    """按脚本回话的假模型。

    answers 里是依次要返回的内容；用完之后一直返回最后一个。
    error 不为空时，每次调用都抛这个异常。
    """

    name = "scripted"

    def __init__(self, *answers: str, error: Exception | None = None):
        self.answers = list(answers) or ["嗯。"]
        self.error = error
        self.calls: list[dict] = []

    async def chat(self, system="", messages=None, **kw) -> Reply:
        self.calls.append({"system": system, "messages": messages, "kw": kw})
        if self.error is not None:
            raise self.error
        text = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return Reply(text=text)

    def capabilities(self):
        from app.adapters.llm.base import Capabilities

        return Capabilities()


def group_msg(
    text: str = "今天股市怎么样",
    *,
    chat_id: str = "group:1",
    sender: str = "111",
    group_name: str = "测试群",
    at: list[str] | None = None,
    reply_to: int | None = None,
) -> InboundMessage:
    return InboundMessage(
        account_id="default",
        chat_id=chat_id,
        chat_type=ChatType.GROUP,
        group_id=chat_id.split(":", 1)[-1],
        group_name=group_name,
        sender_qq=sender,
        sender_nick=f"user{sender}",
        text=text,
        at_list=at or [],
        reply_to=reply_to,
        event_at=time.time(),
    )


def private_msg(text: str = "在吗", *, user: str = "222") -> InboundMessage:
    return InboundMessage(
        account_id="default",
        chat_id=f"private:{user}",
        chat_type=ChatType.PRIVATE,
        sender_qq=user,
        sender_nick=f"user{user}",
        text=text,
        event_at=time.time(),
    )
