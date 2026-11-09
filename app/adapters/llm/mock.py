"""假模型适配器。

两个用途：
  1. 没配 API key 也能把整套流程跑起来（试用、演示、截图）
  2. 端到端测试：真进程 + 真 websocket，但回复是确定的，不花钱

它不是"猜你想说什么"，只是把最后一条用户消息回显一下，
格式统一成 `[mock] 收到：...`，方便断言。
"""

from __future__ import annotations

from .base import BaseAdapter, Capabilities, Reply


class MockAdapter(BaseAdapter):
    """回显 + 计数。确定性输出，方便测试。"""

    name = "mock"

    def __init__(self, *, reply: str | None = None):
        self.reply = reply
        self.calls: list[dict] = []
        self.closed = False

    async def chat(
        self,
        system: str = "",
        messages=None,
        *,
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        **kwargs,
    ) -> Reply:
        self.calls.append({"system": system, "messages": messages, "max_tokens": max_tokens})

        last_user = ""
        for m in messages or []:
            role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "")
            if role != "user":
                continue
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
            last_user = str(content or "")

        text = self.reply if self.reply is not None else f"[mock] 收到：{last_user[:60]}"
        return Reply(
            text=text,
            usage={"prompt_tokens": 0, "completion_tokens": 0},
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("假模型不算向量")

    def capabilities(self) -> Capabilities:
        return Capabilities(tools=False, images=False, streaming=False, max_context=8192)

    async def close(self) -> None:
        self.closed = True

    def __repr__(self) -> str:
        return "<mock adapter>"
