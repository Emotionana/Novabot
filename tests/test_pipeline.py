"""主流程测试（用假模型，不联网）。

跑：python -m tests.test_pipeline
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adapters.llm.base import Capabilities, Reply  # noqa: E402
from app.db import Database  # noqa: E402
from app.models import ChatType, InboundMessage  # noqa: E402


class FakeLLM:
    """假模型：把 system + 最后一条消息回显，方便断言。"""

    name = "fake"

    def __init__(self, answer: str = "嗯。"):
        self.answer = answer
        self.calls: list[dict] = []

    async def chat(self, system="", messages=None, **kw) -> Reply:
        self.calls.append({"system": system, "messages": messages, "kw": kw})
        return Reply(text=self.answer)

    def capabilities(self) -> Capabilities:
        return Capabilities(tools=False, images=False, streaming=False, max_context=8192)


def mk(
    text: str,
    *,
    chat_type: ChatType = ChatType.GROUP,
    sender: str = "111",
    chat_id: str = "group:1",
    at: list[str] | None = None,
    reply_to: int | None = None,
) -> InboundMessage:
    return InboundMessage(
        account_id="default",
        chat_id=chat_id,
        chat_type=chat_type,
        group_id="1" if chat_type == ChatType.GROUP else None,
        group_name="测试群" if chat_type == ChatType.GROUP else None,
        sender_qq=sender,
        sender_nick=f"user{sender}",
        text=text,
        at_list=at or [],
        reply_to=reply_to,
        event_at=time.time(),
    )


async def run() -> int:
    import app.params  # noqa: F401
    from app.core.pipeline import Pipeline
    from app.memory.system import MemorySystem
    from app.settings import Settings

    tmp = Path(tempfile.mkdtemp(prefix="novabot-pipe-"))
    db = Database(tmp / "test.db")
    s = Settings(db)
    s.set("security.owner_qq", "10001")
    s.set("gateway.bot_qq", "10002")
    s.set("reply.wake_words", ["小助手"])
    s.set("reply.max_per_min", 0)      # 关掉限流，专心测流程
    s.set("reply.cooldown_sec", 0)

    mem = MemorySystem(db)
    llm = FakeLLM("嗯，知道了。")
    pipe = Pipeline(settings=s, memory=mem, llm=llm, owner_qq="10001")

    print("=== 群里没叫我 → 不回，但记账")
    r = await pipe.handle(mk("今天好热"))
    print(f"  回了吗：{r.replied}  原因：{r.decision.reason}")
    print(f"  episode id：{r.episode_id}")
    assert not r.replied
    assert r.episode_id is not None, "不回也要记账"

    print()
    print("=== 私聊 → 回")
    r = await pipe.handle(mk("在吗", chat_type=ChatType.PRIVATE, chat_id="private:111"))
    print(f"  回了吗：{r.replied}  内容：{r.text}")
    print(f"  token：{r.tokens}  耗时：{r.duration*1000:.0f}ms")
    assert r.replied
    assert llm.calls, "应该调了模型"

    print()
    print("=== 检查 prompt 里有什么")
    call = llm.calls[-1]
    print(f"  system 长度：{len(call['system'])} 字")
    print(f"  消息条数：{len(call['messages'])}")
    print(f"  最后一条：{call['messages'][-1]['content'][:40]}")
    assert call["messages"][-1]["role"] == "user"

    print()
    print("=== 群里被叫 → 回")
    r = await pipe.handle(mk("小助手你在吗"))
    print(f"  回了吗：{r.replied}  原因：{r.decision.reason}")
    assert r.replied

    print()
    print("=== @ 也算叫我")
    r = await pipe.handle(mk("看这个", at=["10002"]))
    print(f"  回了吗：{r.replied}  原因：{r.decision.reason}")
    assert r.replied

    print()
    print("=== 主人私聊优先")
    r = await pipe.handle(
        mk("嗯", chat_type=ChatType.PRIVATE, chat_id="private:10001", sender="10001")
    )
    print(f"  回了吗：{r.replied}  原因：{r.decision.reason}")
    assert r.replied

    print()
    print("=== 屏蔽词")
    s.set("reply.blacklist_words", ["广告"])
    r = await pipe.handle(mk("发个广告 小助手"))
    print(f"  回了吗：{r.replied}  原因：{r.decision.reason}")
    assert not r.replied

    print()
    print("=== 长回复拆条")
    s.set("output.split_chars", 20)   # 调低阈值方便测
    llm.answer = "第一句话写得长一点凑字数。第二句话也要长一些才行。第三句话继续凑够六十个字。第四句话再补一点点。"
    r = await pipe.handle(
        mk("小助手说点什么", chat_type=ChatType.PRIVATE, chat_id="private:222", sender="222")
    )
    print(f"  拆成 {len(r.messages)} 条：")
    for m in r.messages:
        print(f"    {m.text}")
    assert len(r.messages) > 1, "应该拆条"

    print()
    print("=== Markdown 清理")
    llm.answer = "**加粗** 和 # 标题"
    r = await pipe.handle(
        mk("小助手", chat_type=ChatType.PRIVATE, chat_id="private:333", sender="333")
    )
    print(f"  清理后：{r.text!r} → {r.messages[0].text!r}")
    assert "**" not in r.messages[0].text

    print()
    print("=== 模型出错")
    class FailLLM(FakeLLM):
        async def chat(self, system="", messages=None, **kw) -> Reply:
            return Reply(error="超时")

    pipe2 = Pipeline(settings=s, memory=mem, llm=FailLLM(), owner_qq="10001")
    r = await pipe2.handle(
        mk("小助手", chat_type=ChatType.PRIVATE, chat_id="private:444", sender="444")
    )
    print(f"  回了吗：{r.replied}  错误：{r.error}")
    assert not r.replied and r.error

    print()
    print("=== 没配模型")
    pipe3 = Pipeline(settings=s, memory=mem, llm=None, owner_qq="10001")
    r = await pipe3.handle(
        mk("小助手", chat_type=ChatType.PRIVATE, chat_id="private:555", sender="555")
    )
    print(f"  回了吗：{r.replied}  错误：{r.error}")
    assert not r.replied

    print()
    print("=== 决策解释")
    ex = pipe.explain("group:1", "111", "随便说说")
    print(f"  {ex}")

    print()
    print("=== token 记账")
    summary = pipe.meter.summary()
    print(f"  今日：{summary}")
    assert summary.get("calls", 0) > 0

    print()
    print("=== 回复我 vs 回复别人")
    # 记一条我发过的消息 id
    my_ep = mem.remember(
        InboundMessage(account_id="default", chat_id="group:9", chat_type=ChatType.GROUP,
                       group_id="9", sender_qq="self", sender_nick="小助手", text="我说的")
    )
    pipe.note_bot_reply("group:9", my_ep)

    r = await pipe.handle(mk("对", chat_id="group:9", reply_to=my_ep))
    print(f"  回复我的消息 → 回了吗：{r.replied}（{r.decision.reason}）")
    assert r.replied

    # 换一个新会话，避免上一条的接话窗口干扰
    r = await pipe.handle(mk("对", chat_id="group:10", reply_to=99999))
    print(f"  回复别人的消息 → 回了吗：{r.replied}（{r.decision.reason}）")
    assert not r.replied, "回复别人的消息不该当成叫我"

    db.close()
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
