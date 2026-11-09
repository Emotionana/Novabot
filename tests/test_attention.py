"""注意筛的测试。

跑：python -m tests.test_attention
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Database  # noqa: E402
from app.models import ChatType, InboundMessage  # noqa: E402


def mk_msg(
    text: str = "你好",
    *,
    chat_type: ChatType = ChatType.GROUP,
    sender: str = "111",
    at: list[str] | None = None,
    reply_to: int | None = None,
    chat_id: str = "group:100",
    group_name: str = "测试群",
) -> InboundMessage:
    return InboundMessage(
        account_id="default",
        chat_id=chat_id,
        chat_type=chat_type,
        group_id="100" if chat_type == ChatType.GROUP else None,
        group_name=group_name if chat_type == ChatType.GROUP else None,
        sender_qq=sender,
        sender_nick=f"user{sender}",
        text=text,
        at_list=at or [],
        reply_to=reply_to,
        event_at=time.time(),
    )


def main() -> int:
    import app.params  # noqa: F401
    from app.core.attention import Action, Attention, ChatState
    from app.settings import Settings

    tmp = Path(tempfile.mkdtemp(prefix="novabot-attn-"))
    db = Database(tmp / "test.db")
    s = Settings(db)
    s.set("gateway.bot_qq", "10002")
    s.set("reply.wake_words", ["小助手", "小助"])
    s.set("security.owner_qq", "10001")
    s.set("reply.group_rate_max_msgs", 0)

    attn = Attention(s)
    state = ChatState()

    def check(name: str, msg: InboundMessage, expect: Action, *, owner=False, st=None, last_bot=None):
        d = attn.judge(msg, st or ChatState(), is_owner=owner, last_bot_reply_id=last_bot)
        mark = "✓" if d.action == expect else "✗"
        print(f"  {mark} {name:28s} → {d.action:6s}  {d.reason}")
        if d.action != expect:
            raise AssertionError(f"{name}：期望 {expect}，实际 {d.action}（{d.reason}）")
        return d

    print("=== 基本规则")
    check("群里没叫我", mk_msg("今天天气不错"), Action.IGNORE)
    check("私聊默认必回", mk_msg("在吗", chat_type=ChatType.PRIVATE), Action.REPLY)

    print()
    print("=== @ 和唤醒词")
    check("@ 机器人", mk_msg("@我 你好", at=["10002"]), Action.REPLY)
    check("含唤醒词", mk_msg("小助手你在干嘛"), Action.REPLY)
    check("别名也能叫", mk_msg("小助"), Action.REPLY)
    check("回复我的消息", mk_msg("对", reply_to=123), Action.REPLY, last_bot=123)

    print()
    print("=== 主人")
    check("主人说话", mk_msg("嗯"), Action.REPLY, owner=True)

    print()
    print("=== 噪音筛（@ 了我但内容没东西）")
    check("@ + 纯表情", mk_msg("[CQ:face,id=1]", at=["10002"]), Action.IGNORE)
    check("@ + 单字", mk_msg("嗯", at=["10002"]), Action.IGNORE)
    check("@ + 纯链接", mk_msg("https://b23.tv/xxx", at=["10002"]), Action.IGNORE)
    check("@ + 有内容", mk_msg("今天天气怎么样", at=["10002"]), Action.REPLY)
    check("叫我+表情也算有内容", mk_msg("[CQ:face,id=1] 小助手"), Action.REPLY)

    print()
    print("=== 屏蔽词")
    s.set("reply.blacklist_words", ["广告", "代练"])
    check("含屏蔽词", mk_msg("发个广告 小助手"), Action.IGNORE)

    print()
    print("=== 群聊模式（先关掉限流和冷却，单独测模式）")
    s.set("reply.group_rate_max_msgs", 0)
    s.set("reply.cooldown_sec", 0)
    attn = Attention(s)

    s.set("reply.group_mode", "always")
    check("每条都回", mk_msg("随便说点"), Action.REPLY)

    s.set("reply.group_mode", "never")
    check("从不主动回", mk_msg("小助手你好"), Action.REPLY)  # 被叫还是回
    check("从不回且没叫", mk_msg("随便"), Action.IGNORE)

    s.set("reply.group_mode", "random")
    s.set("reply.probability", 0.0)
    check("随机概率 0", mk_msg("说点啥"), Action.IGNORE)

    s.set("reply.probability", 1.0)
    check("随机概率 100%", mk_msg("说点啥"), Action.REPLY)

    s.set("reply.group_mode", "smart")
    d = check("智能模式→交给模型", mk_msg("今天股市怎么样"), Action.DEFER)
    print(f"       提示：{d.hint[:40]}…")

    print()
    print("=== 防抖由 pipeline 统一处理")
    print()
    print("=== 频率限制")
    s.set("reply.group_mode", "always")
    s.set("reply.cooldown_sec", 0)
    s.set("reply.group_rate_window_sec", 60)
    s.set("reply.group_rate_max_msgs", 3)
    attn3 = Attention(s)
    st3 = ChatState()
    msg = mk_msg("刷屏")
    results = []
    for i in range(4):
        d = attn3.judge(msg, st3)
        results.append(d.action)
        print(f"  第 {i+1} 次 → {d.action:6s} {d.reason}")
    assert results[:3] == [Action.REPLY] * 3, f"前三次应该都回：{results}"
    assert results[3] == Action.IGNORE, f"第四次应该不回：{results}"
    print("  ✓ 前三次回，第四次被限流")

    print()
    print("=== 主频率不限")
    s.set("reply.group_rate_max_msgs", 0)
    attn4 = Attention(s)
    st4 = ChatState()
    for i in range(10):
        d = attn4.judge(mk_msg("主人刷屏"), st4, is_owner=True)
    print(f"  主人连发 10 条 → 最后一条 {d.action}")
    assert d.action == Action.REPLY

    print()
    print("=== 重要度打分")
    base = mk_msg("随便")
    print(f"  群聊普通     {attn.score_importance(base):.2f}")
    print(f"  群聊被叫     {attn.score_importance(mk_msg('小助手')):.2f}")
    print(f"  @ 且有问号   {attn.score_importance(mk_msg('小助手 你怎么看？', at=['10002'])):.2f}")
    print(f"  主人私聊     {attn.score_importance(mk_msg('在吗', chat_type=ChatType.PRIVATE), is_owner=True):.2f}")

    print()
    print("=== 继续接话")
    s.set("reply.group_mode", "mention")
    st5 = ChatState(pending_followup=time.time() + 8, followup_sender="111", recent_senders=["111"])
    d = attn.judge(mk_msg("接着刚才那句"), st5)
    print(f"  {'✓' if d.action == Action.REPLY else '✗'} 窗口内接着说    → {d.action}  {d.reason}")

    db.close()
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())


def test_followup_window_only_replies_to_waker(db, settings):
    import app.params  # noqa: F401
    from app.core.attention import Action, Attention, ChatState

    settings.set("gateway.bot_qq", "10002")
    settings.set("reply.wake_words", ["小助"])
    settings.set("reply.followup_sec", 30)
    settings.set("reply.followup_sender_only", True)
    settings.set("reply.group_rate_max_msgs", 0)
    settings.set("reply.group_mode", "mention")

    attn = Attention(settings)
    state = ChatState()
    first = attn.judge(mk_msg("小助", sender="111"), state)
    assert first.action == Action.REPLY
    state.recent_senders = ["111"]

    other = attn.judge(mk_msg("小助", sender="222"), state)
    assert other.action == Action.IGNORE
    assert other.rule == "followup_other_sender"

    same = attn.judge(mk_msg("继续", sender="111"), state)
    assert same.action == Action.REPLY
