"""引用消息：bridge 解析 reply 段，pipeline 用 message_id 找回原消息。"""

from __future__ import annotations

from app.core.pipeline import Pipeline
from app.gateway.bridge import parse_event
from app.models import ChatType, InboundMessage


def _event(message):
    return {
        "post_type": "message",
        "message_type": "private",
        "user_id": "20002",
        "sender": {"nickname": "测试", "card": ""},
        "message_id": "999",
        "message": message,
    }


def test_bridge_reads_reply_segment():
    msg = parse_event(_event([
        {"type": "reply", "data": {"id": "12345"}},
        {"type": "text", "data": {"text": "你看这个"}},
    ]))
    assert msg is not None
    assert msg.reply_message_id == "12345"


def test_bridge_reads_cq_reply():
    msg = parse_event(_event("[CQ:reply,id=67890]你看这个"))
    assert msg is not None
    assert msg.reply_message_id == "67890"


def test_quote_lookup_by_message_id(mem):
    ep_id = mem.remember(InboundMessage(
        account_id="acc-a", chat_id="private:20002", chat_type=ChatType.PRIVATE,
        sender_qq="20002", sender_nick="测试",
        text="被引用的原话", message_id="12345",
        raw={"message_id": 12345},
    ))
    assert ep_id
    p = object.__new__(Pipeline)
    p.memory = mem
    msg = InboundMessage(
        account_id="acc-a", chat_id="private:20002", chat_type=ChatType.PRIVATE,
        sender_qq="20002", text="引用它", reply_message_id="12345",
    )
    quote = p._quote_of_message_id(msg)
    assert "引用" in quote
    assert "被引用的原话" in quote
