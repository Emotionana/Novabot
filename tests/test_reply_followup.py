"""回复唤醒窗口：只接同一个人，不让别人插话触发。"""

from __future__ import annotations

import time

from app.core.attention import Attention, ChatState
from app.models import ChatType, InboundMessage


class S:
    def get(self, key, default=None):
        return {"reply.enabled": True, "gateway.bot_qq": "10002", "reply.followup_sec": 8}.get(key, default)


def _msg(sender: str) -> InboundMessage:
    return InboundMessage(
        chat_id="group:1", chat_type=ChatType.GROUP, group_id="1",
        sender_qq=sender, text="接着说", event_at=time.time(),
    )


def test_followup_only_same_sender():
    state = ChatState()
    state.pending_followup = time.time() + 8
    state.followup_sender = "A"
    state.recent_senders = ["A", "B"]
    attn = Attention(S())
    assert attn._is_followup("接着说", _msg("A"), state) is True
    assert attn._is_followup("我也说", _msg("B"), state) is False


def test_followup_without_sender_does_not_trigger():
    state = ChatState()
    state.pending_followup = time.time() + 8
    state.recent_senders = ["A"]
    attn = Attention(S())
    assert attn._is_followup("接着说", _msg("A"), state) is False


def test_wake_window_is_not_refreshed_by_ordinary_messages():
    state = ChatState()
    attn = Attention(S())
    state.recent_senders = ["A"]
    now = time.time()
    attn._arm_wake(state, _msg("A"), now)
    assert state.followup_sender == "A"
    assert 7.5 < state.pending_followup - now < 8.5
    # 普通消息只读窗口，不会改截止时间
    assert attn._is_followup("接着说", _msg("A"), state) is True
    assert 7.5 < state.pending_followup - now < 8.5
