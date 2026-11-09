"""读循环与发送的互锁回归测试。

旧实现里 `_read_loop` 会 await 事件处理，而事件处理里的发送要等
echo——echo 只能由同一个读循环读到，于是每条回复卡 15s 超时 × 3 次重试。

这个测试用假 websocket 把「收到消息 → 回话 → 发出去」整条链路跑一遍，
3 秒内跑完才算过。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.gateway.onebot import OneBotClient, OneBotConfig
from app.models import OutboundMessage


class FakeWS:
    """够用的假 websocket：读队列、发即回 echo。"""

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.queue.get()
        if item is None:
            raise StopAsyncIteration
        return json.dumps(item)

    async def send(self, payload: str) -> None:
        data = json.loads(payload)
        self.sent.append(data)
        # 服务端立刻回一条带 echo 的响应
        self.queue.put_nowait(
            {"echo": data.get("echo"), "retcode": 0, "data": {"message_id": 7}}
        )

    async def close(self) -> None:
        self.closed = True
        self.queue.put_nowait(None)


def message_event(text: str = "你好") -> dict:
    return {
        "post_type": "message",
        "message_type": "group",
        "group_id": 9,
        "user_id": 111,
        "time": 1730000000,
        "self_id": 1,
        "sender": {"nickname": "阿杰", "card": "阿杰"},
        "message": [{"type": "text", "data": {"text": text}}],
    }


@pytest.mark.asyncio
async def test_send_does_not_deadlock_read_loop():
    handled = asyncio.Event()
    errors: list[str] = []

    async def handler(event: dict) -> None:
        try:
            if event.get("post_type") != "message":
                return
            await asyncio.sleep(0.05)  # 假装在思考 / 调模型
            await client.send(OutboundMessage(chat_id="group:9", text="收到"))
            handled.set()
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {e}")

    client = OneBotClient(OneBotConfig(bot_qq="1", url="ws://test"), on_event=handler)
    fake = FakeWS()
    client.ws = fake
    client.connected = True

    task = asyncio.create_task(client._read_loop())
    fake.queue.put_nowait(message_event())

    await asyncio.wait_for(handled.wait(), timeout=3.0)

    assert not errors, errors
    assert fake.sent, "应该发出过一条消息"
    assert fake.sent[0]["action"] == "send_group_msg"
    assert fake.sent[0]["params"]["group_id"] == 9
    assert client._pending == {}, "echo 应该都被消费掉"

    await client.stop()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_connect_failure_is_handled(monkeypatch):
    """连不上时只记失败次数，不能把进程带崩。"""

    async def refuse(*args, **kwargs):
        raise OSError("connection refused")

    # 不打真网络：把这个实现换成必然失败
    monkeypatch.setattr("websockets.connect", refuse)

    client = OneBotClient(
        OneBotConfig(bot_qq="1", url="ws://127.0.0.1:1", reconnect_sec=1), on_event=None
    )

    await client.start()
    for _ in range(20):
        if client.failures:
            break
        await asyncio.sleep(0.05)

    assert client.connected is False
    assert client.failures >= 1
    assert "connection refused" in client.last_error

    await client.stop()


@pytest.mark.asyncio
async def test_same_chat_is_serialized():
    """同一个会话的两次处理不能交叉，否则回复顺序会乱。"""
    states: list[str] = []
    done = asyncio.Event()
    count = 0

    async def handler(event: dict) -> None:
        nonlocal count
        if event.get("post_type") != "message":
            return
        count += 1
        states.append(f"start-{count}")
        await asyncio.sleep(0.05)
        states.append(f"end-{count}")
        if count >= 2:
            done.set()

    client = OneBotClient(OneBotConfig(bot_qq="1", url="ws://test"), on_event=handler)
    fake = FakeWS()
    client.ws = fake
    client.connected = True

    task = asyncio.create_task(client._read_loop())
    fake.queue.put_nowait(message_event("第一条"))
    fake.queue.put_nowait(message_event("第二条"))

    await asyncio.wait_for(done.wait(), timeout=3.0)

    assert states == ["start-1", "end-1", "start-2", "end-2"], states

    await client.stop()
    await asyncio.wait_for(task, timeout=1.0)
