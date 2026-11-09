"""MCP stdio 协议处理。"""

from __future__ import annotations

from app.mcp_server import McpServer


class FakeProvider:
    def recall(self, **kw):
        return "记忆内容"

    def remember(self, **kw):
        return 1

    def persona_prompt(self, **kw):
        return "人格 prompt"

    def list_sessions(self, limit=50):
        return []


def test_mcp_initialize_and_tools():
    server = McpServer(FakeProvider())
    init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init["result"]["serverInfo"]["name"] == "novabot"

    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in listed["result"]["tools"]}
    assert {"pb_recall", "pb_remember", "pb_persona", "pb_sessions"} <= names


def test_mcp_tool_call():
    server = McpServer(FakeProvider())
    r = server.handle({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "pb_recall", "arguments": {"query": "在吗"}},
    })
    assert r["result"]["content"][0]["text"] == "记忆内容"
