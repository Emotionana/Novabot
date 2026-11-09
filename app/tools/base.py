"""工具调用的最小骨架。

原则：
- 工具声明和执行业务分开
- 权限在执行时再查一次（模型可能乱调）
- 每次调用写一条 tool_calls 审计
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class ToolContext:
    db: Any
    account_id: str = ""
    chat_id: str = ""
    person_qq: str = ""
    is_owner: bool = False
    now: float = field(default_factory=time.time)
    tz: Any = None
    permissions: dict = field(default_factory=dict)

    @property
    def is_private(self) -> bool:
        return self.chat_id.startswith("private:")


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[[ToolContext, dict], str]
    owner_only: bool = False
    write: bool = False

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def available(self, ctx: ToolContext, enabled: list[str] | None = None) -> list[Tool]:
        out = []
        for tool in self.tools.values():
            if enabled and tool.name not in enabled:
                continue
            if tool.owner_only and (not ctx.is_owner or not ctx.is_private):
                continue
            out.append(tool)
        return out

    def specs(self, ctx: ToolContext, enabled: list[str] | None = None) -> list[dict]:
        return [t.schema() for t in self.available(ctx, enabled)]

    def execute(
        self,
        name: str,
        args: dict | None,
        ctx: ToolContext,
        *,
        timeout: float = 10.0,
    ) -> str:
        tool = self.get(name)
        if tool is None:
            self._audit(ctx, name, args or {}, "没有这个工具", False, 0)
            return f"没有这个工具：{name}"
        if tool.owner_only and (not ctx.is_owner or not ctx.is_private):
            self._audit(ctx, name, args or {}, "权限不足", False, 0)
            return "这个工具只能在主人私聊里用"
        if tool.write and not ctx.is_private:
            self._audit(ctx, name, args or {}, "写操作被拒绝", False, 0)
            return "写操作只能在私聊里做"

        started = time.time()
        ok = True
        try:
            result = str(tool.handler(ctx, args or {}))
        except Exception as e:  # noqa: BLE001
            ok = False
            result = f"工具失败：{e}"
            log.warning("工具 %s 失败：%s", name, e)
        duration = int((time.time() - started) * 1000)
        if time.time() - started > timeout:
            result = f"工具超时（>{timeout:.0f}s）"

        self._audit(ctx, name, args or {}, result, ok, duration)
        return result[:800]

    def _audit(self, ctx: ToolContext, name: str, args: dict, result: str, ok: bool, ms: int) -> None:
        try:
            ctx.db.execute(
                "INSERT INTO tool_calls "
                "(account_id, chat_id, person_qq, tool, args, result, ok, duration_ms, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    ctx.account_id,
                    ctx.chat_id,
                    ctx.person_qq,
                    name,
                    json.dumps(args, ensure_ascii=False)[:1000],
                    (result or "")[:1000],
                    1 if ok else 0,
                    ms,
                    time.time(),
                ],
            )
        except Exception as e:  # noqa: BLE001
            log.debug("写工具审计失败：%s", e)


REGISTRY = ToolRegistry()
