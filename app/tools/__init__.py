"""工具调用：声明、执行、提醒服务。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from . import builtin  # noqa: F401  注册内置工具
from . import files as _files  # noqa: F401  注册文件工具
from .base import REGISTRY, Tool, ToolContext
from .reminders import ReminderService

__all__ = [
    "REGISTRY",
    "Tool",
    "ToolContext",
    "ReminderService",
    "tool_specs",
    "execute_tool",
    "resolve_tz",
]


def resolve_tz(settings):
    try:
        name = settings.get("server.timezone") or "Asia/Shanghai"
        return ZoneInfo(str(name))
    except Exception:  # noqa: BLE001
        return datetime.now().astimezone().tzinfo


def tool_specs(ctx: ToolContext, settings) -> list[dict]:
    if not settings.get("tools.enabled"):
        return []
    enabled = settings.get("tools.enabled_list") or []
    return REGISTRY.specs(ctx, enabled)


def execute_tool(name: str, args: dict | None, ctx: ToolContext, settings) -> str:
    timeout = float(settings.get("tools.timeout_sec") or 10)
    return REGISTRY.execute(name, args, ctx, timeout=timeout)
