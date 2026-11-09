"""Novabot MCP Server（stdio）。

供 TRAE / CodeBuddy 等支持 MCP 的插件直连：
    {"mcpServers":{"novabot":{"command":"...python.exe","args":["-m","app.mcp_server"]}}}

只暴露记忆/人格/会话工具，不监听端口。
"""

from __future__ import annotations

import json
import logging
import sys

log = logging.getLogger("novabot.mcp")

TOOLS = [
    {
        "name": "pb_recall",
        "description": "从 Novabot 长期记忆里查这个人的信息、以前说过什么。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "查什么"},
                "who": {"type": "string", "description": "关于谁，QQ 或名字，可空"},
                "session_id": {"type": "string", "description": "外部会话 id，可空"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "pb_remember",
        "description": "把一段值得记住的话存进长期记忆。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要记的内容"},
                "who": {"type": "string", "description": "关于谁，可空"},
                "session_id": {"type": "string", "description": "外部会话 id，可空"},
                "is_self": {"type": "boolean", "description": "是不是助手自己的话"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "pb_persona",
        "description": "取当前人格卡拼成的 system prompt。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "who": {"type": "string"},
                "text": {"type": "string"},
            },
        },
    },
    {
        "name": "pb_sessions",
        "description": "列出已映射的外部平台会话。",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
    },
]


class McpServer:
    def __init__(self, provider):
        self.provider = provider

    def handle(self, msg: dict) -> dict | None:
        method = msg.get("method")
        msg_id = msg.get("id")
        if method == "initialize":
            return {
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "novabot", "version": "0.1.0"},
                },
            }
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
        if method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                text = self.call(name, args)
                return {
                    "jsonrpc": "2.0", "id": msg_id,
                    "result": {"content": [{"type": "text", "text": text}]},
                }
            except Exception as e:  # noqa: BLE001
                return {"jsonrpc": "2.0", "id": msg_id,
                        "error": {"code": -32603, "message": str(e)[:300]}}
        if msg_id is None:
            return None
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32601, "message": f"未知方法：{method}"}}

    def call(self, name: str, args: dict) -> str:
        p = self.provider
        if name == "pb_recall":
            session = str(args.get("session_id") or "mcp")
            text = p.recall(
                platform="mcp", external_id=session,
                sender_qq=str(args.get("who") or "unknown"),
                text=str(args.get("query") or ""), max_chars=500,
            )
            return text or "没查到相关记忆。"
        if name == "pb_remember":
            session = str(args.get("session_id") or "mcp")
            ep = p.remember(
                platform="mcp", external_id=session,
                sender_qq=str(args.get("who") or "unknown"),
                text=str(args.get("text") or ""),
                is_self=bool(args.get("is_self")),
            )
            return "记住了。" if ep is not None else "没记成。"
        if name == "pb_persona":
            return p.persona_prompt(
                chat_id=str(args.get("session_id") or ""),
                sender_qq=str(args.get("who") or ""),
                text=str(args.get("text") or ""),
            ) or "（没有人格设定）"
        if name == "pb_sessions":
            items = p.list_sessions(limit=int(args.get("limit") or 50))
            return "\n".join(f"{s.platform}:{s.external_id} -> {s.chat_id}" for s in items) or "（没有会话映射）"
        raise ValueError(f"未知工具：{name}")


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    from .adapters.platform.dsh import PlatformProvider
    from .db import get_db
    from .memory.system import MemorySystem
    from .persona import PersonaSystem
    from .settings import Settings

    db = get_db()
    settings = Settings(db)
    memory = MemorySystem(db)
    persona = PersonaSystem(db, settings)
    provider = PlatformProvider(db, memory, persona)
    server = McpServer(provider)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = server.handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
