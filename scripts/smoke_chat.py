"""手动冒烟：跑两轮对话，看看 prompt 里到底有什么。

用法：
    python -m scripts.smoke_chat           # 假模型，不需要 API key
    python -m scripts.smoke_chat --real    # 用 config/local.toml 里配好的真模型

它做的事：
    1. 建一个临时库（不碰 data/novabot.db）
    2. 用真 Pipeline 走两轮对话
    3. 把第二轮实际发给模型的消息打出来
    4. 检查：历史里有 assistant、当前消息没重复、回复落了库
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app import params as _params  # noqa: E402,F401  注册参数
from app.adapters.llm.base import Reply  # noqa: E402
from app.core.pipeline import Pipeline  # noqa: E402
from app.db import Database  # noqa: E402
from app.memory.migrate import init_schema  # noqa: E402
from app.memory.system import MemorySystem  # noqa: E402
from app.models import ChatType, InboundMessage  # noqa: E402
from app.settings import Settings  # noqa: E402


class EchoLLM:
    """假模型：把看到的消息条数回显出来，方便断言。"""

    name = "echo"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def chat(self, system="", messages=None, **kw) -> Reply:
        self.calls.append({"system": system, "messages": messages, "kw": kw})
        n = len(messages or [])
        return Reply(text=f"看到 {n} 条历史。", usage={"prompt_tokens": 100, "completion_tokens": 8})


def msg(text: str) -> InboundMessage:
    return InboundMessage(
        platform="smoke",
        account_id="smoke",
        chat_id="private:10001",
        chat_type=ChatType.PRIVATE,
        sender_qq="10001",
        sender_nick="小林",
        text=text,
        event_at=time.time(),
        recv_at=time.time(),
    )


async def run(use_real: bool) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="novabot-smoke-"))
    atexit.register(shutil.rmtree, tmp, True)
    db = Database(tmp / "smoke.db")
    init_schema(db)

    settings = Settings(db)
    settings.set("reply.max_per_min", 0)
    settings.set("reply.cooldown_sec", 0)
    settings.set("security.owner_qq", "99999")
    mem = MemorySystem(db)

    if use_real:
        from app.adapters.llm.factory import build_llm

        cfg = config.get()
        llm = build_llm(cfg, settings)
        if llm is None:
            print("！没配模型：请在 config/local.toml 里写 [llm.xxx]，并设置 llm.default")
            db.close()
            return 2
        print(f"真模型：{llm}")
    else:
        llm = EchoLLM()
        print("假模型（加 --real 用真模型）")

    pipe = Pipeline(settings=settings, memory=mem, llm=llm, meter=None, bot_name="小助手")

    print("\n=== 第一轮：我叫小林，在杭州上班")
    r1 = await pipe.handle(msg("我叫小林，在杭州上班"))
    print(f"  回了吗：{r1.replied}  内容：{r1.text!r}  tokens={r1.tokens}")
    print(f"  写入 episode={r1.episode_id}  回复 episode={r1.bot_episode_id}")

    print("\n=== 第二轮：我住哪？")
    r2 = await pipe.handle(msg("我住哪？"))
    print(f"  回了吗：{r2.replied}  内容：{r2.text!r}")

    call = llm.calls[-1]
    print("\n--- 实际发给模型的 system ---")
    print(call["system"] or "（空）")
    print("\n--- 实际发给模型的 messages ---")
    for m in call["messages"]:
        print(f"  [{m['role']}] {m['content'][:60]}")

    roles = [m["role"] for m in call["messages"]]
    problems: list[str] = []

    if not r1.replied or not r2.replied:
        problems.append("两轮都该回")
    if "assistant" not in roles:
        problems.append("历史里没有 assistant：模型看不到自己说过什么")
    if roles.count("user") != 2:
        problems.append(f"当前消息可能重复了：user 出现 {roles.count('user')} 次")
    if r2.bot_episode_id is None:
        problems.append("回复没有写进记忆")

    episodes = mem.hippocampus.recent("private:10001")
    print(f"\n=== 库里这个会话有 {len(episodes)} 条 episode")
    for e in episodes:
        who = "我" if e.sender_qq == "self" else e.sender_nick
        print(f"  [{who}] {e.text}")

    if db.query_one("SELECT 1 FROM people WHERE qq = 'self'"):
        problems.append("people 表里多了个叫 self 的人")

    db.close()

    if problems:
        print("\n！冒烟失败：")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("\n冒烟通过")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Novabot 对话冒烟")
    parser.add_argument("--real", action="store_true", help="用配置里的真模型")
    args = parser.parse_args()
    return asyncio.run(run(args.real))


if __name__ == "__main__":
    sys.exit(main())
