"""模型适配器接口。

所有适配器实现这个协议，核心流程只认接口不认实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class Message:
    role: str        # system / user / assistant / tool
    content: str
    name: str | None = None
    tool_call_id: str | None = None


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(slots=True)
class Reply:
    """模型返回。"""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    raw: Any = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(slots=True)
class Capabilities:
    """适配器支持什么。"""

    tools: bool = False
    images: bool = False
    streaming: bool = False
    max_context: int = 8192
    supports_temperature: bool = True


@runtime_checkable
class ModelAdapter(Protocol):
    """模型适配器。"""

    name: str

    async def chat(
        self,
        system: str = "",
        messages: list[Message] | list[dict] | None = None,
        *,
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        **kwargs,
    ) -> Reply:
        """调一次模型。"""
        ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """算向量。不支持就抛 NotImplementedError。"""
        ...

    def capabilities(self) -> Capabilities:
        ...


class BaseAdapter:
    """适配器基类，提供一些公用逻辑。"""

    name = "base"

    def _normalize(self, system: str, messages) -> list[dict]:
        """把各种形式的 messages 统一成 [{"role":..., "content":...}]。"""
        out: list[dict] = []
        if system:
            out.append({"role": "system", "content": system})

        for m in messages or []:
            if isinstance(m, dict):
                role = m.get("role", "user")
                content = m.get("content", "")
                item = {"role": role, "content": content}
                if m.get("name"):
                    item["name"] = m["name"]
                if m.get("tool_call_id"):
                    item["tool_call_id"] = m["tool_call_id"]
                out.append(item)
            else:
                item = {"role": m.role, "content": m.content}
                if getattr(m, "name", None):
                    item["name"] = m.name
                out.append(item)

        return out
