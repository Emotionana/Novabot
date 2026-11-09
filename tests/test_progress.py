"""长思考进度：B 提示、C 工具动作、撤回清理。"""

from __future__ import annotations

import pytest

from app.gateway.progress import ProgressTurn


class FakeClient:
    def __init__(self):
        self.sent: list[str] = []
        self.deleted: list[str] = []
        self.n = 0

    async def send(self, outbound):
        self.n += 1
        self.sent.append(outbound.text)
        return {"data": {"message_id": f"m{self.n}"}}

    async def delete_msg(self, mid):
        self.deleted.append(mid)


class FakeSettings:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.mark.asyncio
async def test_progress_sends_and_cancel_deletes():
    client = FakeClient()
    turn = ProgressTurn(
        client=client,
        chat_id="private:1",
        account_id="a",
        message_id="u1",
        settings=FakeSettings(**{
            "reply.thinking_progress": True,
            "reply.thinking_max": 3,
        }),
    )
    assert await turn.send("我理一下，稍等。") is True
    assert client.sent == ["我理一下，稍等。"]
    assert turn.message_ids == ["m1"]

    await turn.cancel()
    assert client.deleted == ["m1"]
    assert turn.canceled is True
    assert await turn.send("不该再发") is False


@pytest.mark.asyncio
async def test_progress_does_not_send_formulaic_tool_lines():
    client = FakeClient()
    turn = ProgressTurn(
        client=client,
        chat_id="private:1",
        account_id="a",
        message_id="u1",
        settings=FakeSettings(**{
            "reply.thinking_progress": True,
            "reply.thinking_tools": True,
            "reply.thinking_max": 3,
        }),
    )
    assert await turn.emit_tool("tool/call", "read_file") is False
    assert await turn.emit_tool("tool/result", "read_file") is False
    assert client.sent == []


@pytest.mark.asyncio
async def test_progress_disabled():
    client = FakeClient()
    turn = ProgressTurn(
        client=client,
        chat_id="private:1",
        account_id="a",
        message_id="u1",
        settings=FakeSettings(**{"reply.thinking_progress": False}),
    )
    assert await turn.send("x") is False
    assert client.sent == []


@pytest.mark.asyncio
async def test_recall_cancels_turn_and_deletes_progress():
    import asyncio
    from types import SimpleNamespace

    from app.gateway.manager import AccountRuntime, GatewayManager

    client = FakeClient()
    turn = ProgressTurn(
        client=client,
        chat_id="private:1",
        account_id="a",
        message_id="u1",
        settings=FakeSettings(**{"reply.thinking_progress": True}),
    )
    await turn.send("我理一下。")
    task = asyncio.create_task(asyncio.sleep(30))

    class ManagerSettings:
        def get(self, key, default=None):
            return default

    manager = GatewayManager(ManagerSettings())
    manager.accounts["a"] = AccountRuntime(
        id="a", pipeline=SimpleNamespace(settings=FakeSettings(**{"reply.recall_cancels": True}))
    )
    manager._pending[("a", "u1")] = {"task": task, "progress": turn}

    await manager._handle_recall("a", {"message_id": "u1"})

    assert turn.canceled is True
    assert client.deleted == ["m1"]
    await asyncio.sleep(0)
    assert task.cancelled()
    assert ("a", "u1") not in manager._pending


@pytest.mark.asyncio
async def test_tool_progress_endpoint():
    from app.web.progress_api import ProgressIn
    from app.web.progress_api import progress as endpoint

    client = FakeClient()
    turn = ProgressTurn(
        client=client,
        chat_id="private:1",
        account_id="a",
        message_id="u1",
        settings=FakeSettings(**{
            "reply.thinking_progress": True,
            "reply.thinking_tools": True,
        }),
    )
    result = await endpoint(ProgressIn(
        token=turn.token,
        turn_id=turn.turn_id,
        type="assistant/commentary",
        text="我先看一下你前面提到的记录。",
    ))
    assert result["ok"] is True
    assert client.sent == ["我先看一下你前面提到的记录。"]


def test_message_event_keeps_message_id():
    from app.gateway.bridge import parse_event

    msg = parse_event({
        "post_type": "message",
        "message_type": "private",
        "message_id": 12345,
        "self_id": 1,
        "user_id": 2,
        "time": 1,
        "message": [{"type": "text", "data": {"text": "hi"}}],
        "sender": {"nickname": "u"},
    })
    assert msg is not None
    assert msg.message_id == "12345"


@pytest.mark.asyncio
async def test_assistant_commentary_preferred_over_fallback():
    client = FakeClient()
    turn = ProgressTurn(
        client=client,
        chat_id="private:1",
        account_id="a",
        message_id="u1",
        settings=FakeSettings(**{
            "reply.thinking_progress": True,
            "reply.thinking_tools": True,
        }),
    )
    await turn.emit_commentary("我先看一下你前面提到的记录。")
    assert client.sent == ["我先看一下你前面提到的记录。"]
    assert turn.real_progress is True
