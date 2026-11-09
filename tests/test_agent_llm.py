"""外部 agent 适配器：回复、会话绑定、错误解析。"""

from __future__ import annotations

import httpx
import pytest

from app.adapters.llm.agent import AgentAdapter


def test_agent_reply_and_session_binding(db):
    seen = {}

    def handler(request: httpx.Request):
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, json={
            "reply": "你好呀", "session_id": "dsh-abc", "usage": {"total_tokens": 12},
        })

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = AgentAdapter(platform="dsh", endpoint="http://agent.local/chat",
                               db=db, client=client)
        reply = await adapter.chat(
            system="你是测试", messages=[{"role": "user", "content": "在吗"}],
            session_id="private:1", account_id="acc",
        )
        await adapter.close()
        return reply

    reply = pytest.importorskip("asyncio").run(run())

    assert reply.ok and reply.text == "你好呀"
    assert seen["session_id"] == "private:1"
    assert seen["messages"][0]["content"] == "在吗"
    row = db.query_one(
        "SELECT chat_id FROM platform_sessions WHERE platform='dsh' AND external_id='dsh-abc'"
    )
    assert row["chat_id"] == "private:1"


def test_factory_builds_agent_from_settings(db, settings):
    from app import config
    from app.adapters.llm.factory import build_llm

    settings.set("llm.agent_enabled", True)
    settings.set("llm.agent_type", "dsh")
    settings.set("llm.agent_endpoint", "http://127.0.0.1:9/novabot/api/chat")
    llm = build_llm(config.get(), settings)
    assert llm is not None and llm.name == "agent"
    assert llm.platform == "dsh"

def test_agent_sends_session_policy(db, settings):
    settings.set("llm.group_session_compact_turns", 12)
    settings.set("llm.group_session_rotate_turns", 40)
    seen = {}

    def handler(request: httpx.Request):
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"reply": "ok"})

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = AgentAdapter(
            platform="dsh", endpoint="http://agent.local/chat", db=db, settings=settings, client=client,
        )
        await adapter.chat(
            messages=[{"role": "user", "content": "hi"}],
            session_id="group:123", account_id="acc",
        )
        await adapter.close()

    pytest.importorskip("asyncio").run(run())
    assert seen["session_id"] == "group:123"
    assert seen["account_id"] == "acc"
    assert seen["session_policy"] == {"compact_turns": 12, "rotate_turns": 40}
    assert seen["agent_timeout_sec"] == 600
    assert seen["long_task_mode"] == "manual"
    assert seen["long_task_max_rounds"] == 3


def test_agent_switches_new_session_when_archived(db):
    import asyncio
    import json

    session_ids = []

    def handler(request: httpx.Request):
        payload = json.loads(request.content)
        session_ids.append(payload["session_id"])
        if len(session_ids) == 1:
            return httpx.Response(500, json={"ok": False, "error": "session archived not found"})
        return httpx.Response(200, json={"reply": "新会话回复"})

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = AgentAdapter(platform="dsh", endpoint="http://agent.local/chat",
                               db=db, client=client, max_retries=1)
        reply = await adapter.chat(messages=[{"role": "user", "content": "hi"}],
                                   session_id="private:1", account_id="acc")
        await adapter.close()
        return reply

    reply = asyncio.run(run())
    assert reply.ok and reply.text == "新会话回复"
    assert session_ids == ["private:1", "private:1#nb1"]


def test_agent_steer_injects_into_active_turn(db):
    import asyncio
    import json

    seen = {}

    def handler(request: httpx.Request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "accepted": True})

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = AgentAdapter(platform="dsh", endpoint="http://agent.local/chat",
                               db=db, client=client)
        ok = await adapter.steer("接着说", session_id="private:1", account_id="acc")
        await adapter.close()
        return ok

    assert asyncio.run(run()) is True
    assert seen["steer"] is True
    assert seen["session_id"] == "private:1"
    assert seen["messages"][0]["content"] == "接着说"


def test_private_session_does_not_rotate_by_default(db, settings):
    import asyncio
    import json

    seen = {}

    def handler(request: httpx.Request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"reply": "ok"})

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = AgentAdapter(platform="dsh", endpoint="http://agent.local/chat",
                               db=db, settings=settings, client=client)
        await adapter.chat(messages=[{"role": "user", "content": "hi"}],
                           session_id="private:1", account_id="acc")
        await adapter.close()

    asyncio.run(run())
    assert seen["session_policy"]["compact_turns"] == 30
    assert "rotate_turns" not in seen["session_policy"]
