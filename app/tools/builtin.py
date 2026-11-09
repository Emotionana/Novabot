"""内置工具：时间、笔记、提醒。

全部是低风险操作：
- current_time：任何人、任何会话
- 笔记/提醒：只能在主人私聊里读写（由 Tool.owner_only 控制）
"""

from __future__ import annotations

import time
from datetime import datetime

from .base import REGISTRY, Tool, ToolContext
from .when import format_when, parse_when


def _now(ctx: ToolContext) -> datetime:
    tz = ctx.tz or datetime.now().astimezone().tzinfo
    return datetime.fromtimestamp(ctx.now, tz)


def current_time(ctx: ToolContext, args: dict) -> str:
    dt = _now(ctx)
    week = "一二三四五六日"[dt.weekday()]
    return datetime.fromtimestamp(ctx.now, dt.tzinfo).strftime(
        f"%Y-%m-%d %H:%M 周{week}"
    )


def add_note(ctx: ToolContext, args: dict) -> str:
    body = str(args.get("body") or "").strip()
    if not body:
        raise ValueError("笔记内容不能为空")
    title = str(args.get("title") or "").strip() or body[:20]
    now = time.time()
    ctx.db.execute(
        "INSERT INTO notes (account_id, person_qq, chat_id, title, body, tags, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [ctx.account_id, ctx.person_qq, ctx.chat_id, title, body[:5000], "[]", now, now],
    )
    return f"记下了：{title}"


def search_notes(ctx: ToolContext, args: dict) -> str:
    query = str(args.get("query") or "").strip()
    if query:
        rows = ctx.db.query(
            "SELECT title, body, created_at FROM notes "
            "WHERE account_id = ? AND person_qq = ? AND (title LIKE ? OR body LIKE ?) "
            "ORDER BY created_at DESC LIMIT 5",
            [ctx.account_id, ctx.person_qq, f"%{query}%", f"%{query}%"],
        )
    else:
        rows = ctx.db.query(
            "SELECT title, body, created_at FROM notes "
            "WHERE account_id = ? AND person_qq = ? ORDER BY created_at DESC LIMIT 5",
            [ctx.account_id, ctx.person_qq],
        )
    if not rows:
        return "没有找到笔记"
    lines = []
    for r in rows:
        when = datetime.fromtimestamp(r["created_at"]).strftime("%m-%d")
        lines.append(f"- {when} {r['title']}：{str(r['body'])[:80]}")
    return "\n".join(lines)


def add_reminder(ctx: ToolContext, args: dict) -> str:
    text = str(args.get("text") or "").strip()
    when = str(args.get("when") or "").strip()
    if not text:
        raise ValueError("提醒内容不能为空")
    if not when:
        raise ValueError("提醒时间不能为空")
    tz = ctx.tz or datetime.now().astimezone().tzinfo
    due = parse_when(when, now=ctx.now, tz=tz)
    now = time.time()
    ctx.db.execute(
        "INSERT INTO reminders "
        "(account_id, person_qq, chat_id, text, due_at, status, attempts, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [ctx.account_id, ctx.person_qq, ctx.chat_id, text[:500], due, "pending", 0, now],
    )
    return f"好，{format_when(due, tz)} 提醒你：{text}"


def list_reminders(ctx: ToolContext, args: dict) -> str:
    rows = ctx.db.query(
        "SELECT text, due_at FROM reminders "
        "WHERE account_id = ? AND person_qq = ? AND status = 'pending' "
        "ORDER BY due_at LIMIT 5",
        [ctx.account_id, ctx.person_qq],
    )
    if not rows:
        return "没有待提醒的事"
    tz = ctx.tz or datetime.now().astimezone().tzinfo
    return "\n".join(f"- {format_when(r['due_at'], tz)} {r['text']}" for r in rows)


REGISTRY.register(
    Tool(
        name="current_time",
        description="看现在几点、星期几。回答时间类问题前先调它。",
        parameters={"type": "object", "properties": {}},
        handler=current_time,
    )
)
REGISTRY.register(
    Tool(
        name="add_note",
        description="记一条笔记（想法、待办、资料）。只在主人私聊可用。",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "短标题，可省略"},
                "body": {"type": "string", "description": "笔记内容"},
            },
            "required": ["body"],
        },
        handler=add_note,
        owner_only=True,
        write=True,
    )
)
REGISTRY.register(
    Tool(
        name="search_notes",
        description="按关键词查笔记；不传关键词就列最近的。只在主人私聊可用。",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "关键词，可省略"}},
        },
        handler=search_notes,
        owner_only=True,
    )
)
REGISTRY.register(
    Tool(
        name="add_reminder",
        description=(
            "加一个定时提醒。when 支持 20:30、明天 20:30、10分钟后、2小时后、"
            "2026-11-07 20:30。只在主人私聊可用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "提醒内容"},
                "when": {"type": "string", "description": "什么时候提醒"},
            },
            "required": ["text", "when"],
        },
        handler=add_reminder,
        owner_only=True,
        write=True,
    )
)
REGISTRY.register(
    Tool(
        name="list_reminders",
        description="看还没到点的提醒。只在主人私聊可用。",
        parameters={"type": "object", "properties": {}},
        handler=list_reminders,
        owner_only=True,
    )
)
