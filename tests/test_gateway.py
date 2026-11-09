"""网关测试（不连真 QQ，用假事件）。

跑：python -m tests.test_gateway
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    import app.params  # noqa: F401
    from app.gateway.bridge import parse_event, split_message
    from app.models import ChatType, MsgType, OutboundMessage

    print("=== 解析群消息（array 格式）")
    ev = {
        "post_type": "message",
        "message_type": "group",
        "self_id": 10002,
        "user_id": 10001,
        "group_id": 573560374,
        "time": 1730000000,
        "sender": {"user_id": 10001, "nickname": "TestUser", "card": "主人", "role": "owner"},
        "message": [
            {"type": "at", "data": {"qq": "10002"}},
            {"type": "text", "data": {"text": " 小助手你在吗"}},
        ],
    }
    m = parse_event(ev)
    print(f"  chat_id={m.chat_id} 类型={m.chat_type} 发送者={m.sender_qq}({m.sender_card})")
    print(f"  文本={m.text!r} @={m.at_list} 级别={m.sender_level}")
    assert m.chat_id == "group:573560374"
    assert m.chat_type == ChatType.GROUP
    assert m.sender_card == "主人"
    assert m.text == "小助手你在吗", f"正文不该带 @ 号：{m.text!r}"
    assert "10002" in m.at_list
    assert m.sender_level.value == "owner"

    print()
    print("=== 解析私聊")
    ev2 = {
        "post_type": "message",
        "message_type": "private",
        "user_id": 111,
        "time": 1730000001,
        "sender": {"nickname": "小明"},
        "message": [{"type": "text", "data": {"text": "你好"}}],
    }
    m2 = parse_event(ev2)
    print(f"  chat_id={m2.chat_id} 类型={m2.chat_type} 文本={m2.text!r}")
    assert m2.chat_id == "private:111"
    assert m2.chat_type == ChatType.PRIVATE

    print()
    print("=== 机器人自己发的要过滤")
    ev3 = dict(ev)
    ev3["user_id"] = 10002
    assert parse_event(ev3) is None, "自己的消息不该进来"
    print("  ✓ 过滤掉了")

    print()
    print("=== 非消息事件要过滤")
    assert parse_event({"post_type": "meta_event", "meta_event_type": "heartbeat"}) is None
    assert parse_event({"post_type": "notice", "notice_type": "group_recall"}) is None
    print("  ✓ 心跳和通知都过滤了")

    print()
    print("=== 图片和语音")
    ev4 = {
        "post_type": "message", "message_type": "group", "user_id": 1, "group_id": 2,
        "sender": {"nickname": "A"},
        "message": [{"type": "image", "data": {"file": "x.jpg"}}],
    }
    m4 = parse_event(ev4)
    print(f"  纯图片：文本={m4.text!r} 类型={m4.msg_type}")
    assert m4.msg_type == MsgType.IMAGE

    ev5 = {
        "post_type": "message", "message_type": "group", "user_id": 1, "group_id": 2,
        "sender": {"nickname": "A"},
        "message": [{"type": "record", "data": {"file": "x.amr"}}],
    }
    m5 = parse_event(ev5)
    print(f"  语音：文本={m5.text!r} 类型={m5.msg_type}")
    assert m5.msg_type == MsgType.VOICE

    print()
    print("=== CQ 码格式（老实现用）")
    ev6 = {
        "post_type": "message", "message_type": "group", "user_id": 1, "group_id": 2,
        "sender": {"nickname": "A"},
        "message": "[CQ:at,qq=123] 你好啊",
    }
    m6 = parse_event(ev6)
    print(f"  文本={m6.text!r} @={m6.at_list}")
    assert m6.text == "你好啊"
    assert "123" in m6.at_list

    print()
    print("=== 长消息拆分")
    long_text = "第一句话。" * 100
    parts = split_message(long_text, max_chars=50)
    print(f"  {len(long_text)} 字拆成 {len(parts)} 条，每条最多 {max(len(p) for p in parts)} 字")
    assert len(parts) > 1
    assert all(len(p) <= 55 for p in parts)
    print(f"  第一条：{parts[0][:30]}…")
    print(f"  最后条：{parts[-1][:30]}…")

    print()
    print("=== 短消息不拆")
    assert split_message("你好", max_chars=50) == ["你好"]
    assert split_message("", max_chars=50) == []
    print("  ✓ 短的不拆，空的不发")

    print()
    print("=== 出站消息构造")
    ob = OutboundMessage(chat_id="group:123", text="收到", at=["111"])
    print(f"  chat_id={ob.chat_id} text={ob.text!r} at={ob.at}")

    print()
    print("=== 客户端状态")
    from app.gateway.onebot import OneBotClient, OneBotConfig

    cfg = OneBotConfig(bot_qq="10002", url="ws://127.0.0.1:3001", impl="snowluma")
    cli = OneBotClient(cfg)
    st = cli.status()
    print(f"  状态：{st}")
    assert st["connected"] is False
    assert st["bot_qq"] == "10002"

    print()
    print("=== 实现差异表")
    from app.gateway.onebot import IMPL_PROFILES, profile_of

    for name in IMPL_PROFILES:
        p = profile_of(name)
        print(f"  {name:12s} card_path={p['card_path']:16s} array={p['array_format']}")

    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
