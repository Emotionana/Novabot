"""戳一戳：转成消息，并默认当唤醒词。"""

from __future__ import annotations

import time

from app.core.attention import Action, Attention, ChatState
from app.gateway.bridge import parse_event
from app.models import ChatType, InboundMessage


def _poke_event(**extra):
    event = {
        "post_type": "notice",
        "notice_type": "notify",
        "sub_type": "poke",
        "self_id": "10002",
        "user_id": "20001",
        "target_id": "10002",
        "group_id": "30001",
        "time": time.time(),
        "sender": {"nickname": "小明", "card": "小明"},
    }
    event.update(extra)
    return event


def test_parse_group_poke():
    msg = parse_event(_poke_event())
    assert msg is not None
    assert msg.poke is True
    assert msg.chat_type is ChatType.GROUP
    assert msg.chat_id == "group:30001"
    assert msg.sender_qq == "20001"
    assert "小明" in msg.text
    assert "戳了戳你" in msg.text


def test_poke_for_someone_else_ignored():
    event = _poke_event(target_id="99999")
    assert parse_event(event) is None


class _Settings:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def _msg():
    return InboundMessage(
        chat_id="group:30001",
        chat_type=ChatType.GROUP,
        group_id="30001",
        sender_qq="20001",
        text="[戳一戳] 小明 戳了戳你",
        poke=True,
        event_at=time.time(),
    )


def test_poke_triggers_mention_only():
    attn = Attention(_Settings({
        "reply.enabled": True,
        "reply.group_mode": "mention",
        "gateway.bot_qq": "10002",
        "reply.poke_wake": True,
    }))
    d = attn.judge(_msg(), ChatState())
    assert d.action is Action.REPLY


def test_poke_can_be_disabled():
    attn = Attention(_Settings({
        "reply.enabled": True,
        "reply.group_mode": "mention",
        "gateway.bot_qq": "10002",
        "reply.poke_wake": False,
    }))
    d = attn.judge(_msg(), ChatState())
    assert d.action is Action.IGNORE
