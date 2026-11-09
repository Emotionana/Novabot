"""频率限制：时间窗口 + 条数上限，只有真的回了才占配额。"""

from __future__ import annotations

import time

from app.core.attention import Action, Attention, ChatState
from tests.helpers import group_msg


def test_rate_quota_is_committed_after_debounce(settings):
    settings.set("reply.group_mode", "always")
    settings.set("reply.group_rate_window_sec", 60)
    settings.set("reply.group_rate_max_msgs", 1)
    settings.set("reply.cooldown_sec", 8)

    attn = Attention(settings)
    msg = group_msg("随便说说")
    state = ChatState()

    # 防抖期间先不记账，连续候选不会把配额吃光
    assert attn.judge(msg, state, defer_commit=True).action == Action.REPLY
    assert attn.judge(msg, state, defer_commit=True).action == Action.REPLY
    attn.commit_pending_rate()
    d = attn.judge(msg, state, defer_commit=True)
    assert d.action == Action.IGNORE
    assert d.rule == "rate_limit"


def test_rate_limit_zero_means_unlimited(settings):
    settings.set("reply.group_mode", "always")
    settings.set("reply.group_rate_max_msgs", 0)
    settings.set("reply.cooldown_sec", 0)

    attn = Attention(settings)
    msg = group_msg("主人说话")
    state = ChatState()

    for _ in range(5):
        assert attn.judge(msg, state, is_owner=True).action == Action.REPLY


def test_rate_limit_resets_after_window(settings):
    settings.set("reply.group_mode", "always")
    settings.set("reply.group_rate_window_sec", 1)
    settings.set("reply.group_rate_max_msgs", 1)
    settings.set("reply.cooldown_sec", 0)

    attn = Attention(settings)
    state = ChatState()
    msg = group_msg("刷屏")

    assert attn.judge(msg, state).action == Action.REPLY
    assert attn.judge(msg, state).action == Action.IGNORE

    # 把已用配额挪到窗口外
    attn._window_counts[f"{msg.account_id}:{msg.chat_id}:{msg.sender_qq}"] = [time.time() - 2]
    assert attn.judge(msg, state).action == Action.REPLY


def test_group_rate_is_per_sender(settings):
    settings.set("reply.group_mode", "always")
    settings.set("reply.group_rate_window_sec", 60)
    settings.set("reply.group_rate_max_msgs", 1)
    settings.set("reply.cooldown_sec", 0)

    attn = Attention(settings)
    state = ChatState()
    a = group_msg("A 说", sender="111")
    b = group_msg("B 说", sender="222")

    assert attn.judge(a, state).action == Action.REPLY
    assert attn.judge(a, state).action == Action.IGNORE
    assert attn.judge(b, state).action == Action.REPLY


def test_owner_and_private_skip_group_rate_when_no_cooldown(settings):
    settings.set("reply.group_mode", "always")
    settings.set("reply.group_rate_window_sec", 60)
    settings.set("reply.group_rate_max_msgs", 1)
    settings.set("reply.cooldown_sec", 0)
    settings.set("reply.owner_always", True)
    settings.set("reply.private_always", True)

    attn = Attention(settings)
    owner_state = ChatState()
    owner = group_msg("主人", sender="10001")
    for _ in range(3):
        assert attn.judge(owner, owner_state, is_owner=True).action == Action.REPLY
    private_state = ChatState()
    private = __import__("tests.helpers", fromlist=["private_msg"]).private_msg("在吗", user="10001")
    for _ in range(3):
        assert attn.judge(private, private_state).action == Action.REPLY


def test_rate_limiter_is_per_bot_instance(settings):
    settings.set("reply.group_mode", "always")
    settings.set("reply.group_rate_window_sec", 60)
    settings.set("reply.group_rate_max_msgs", 1)
    settings.set("reply.cooldown_sec", 0)

    a = Attention(settings)
    b = Attention(settings)
    state = ChatState()
    msg = group_msg("同一个群", sender="111")

    assert a.judge(msg, state).action == Action.REPLY
    assert a.judge(msg, state).action == Action.IGNORE
    assert b.judge(msg, state).action == Action.REPLY
