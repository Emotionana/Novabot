"""手动冒烟：原话 → 蒸馏 → 事实 → 检索上下文。

用法：
    python -m scripts.smoke_memory          # 假模型，不花钱
    python -m scripts.smoke_memory --real   # 用真模型（要配好 [llm.xxx]）

它验证的是"记忆闭环"的最后一环：
    写三条原话 → 跑一轮蒸馏 → facts 有行 → build_context 里出现"记得："
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app import params as _params  # noqa: E402,F401
from app.adapters.llm.base import Reply  # noqa: E402
from app.db import Database  # noqa: E402
from app.memory.distill import Distiller  # noqa: E402
from app.memory.migrate import init_schema  # noqa: E402
from app.memory.system import MemorySystem  # noqa: E402
from app.models import ChatType, InboundMessage  # noqa: E402
from app.settings import Settings  # noqa: E402

FAKE_JSON = json.dumps(
    {
        "facts": [
            {
                "subject": "10001",
                "predicate": "住在",
                "object": "杭州",
                "text": "住在杭州",
                "tier": 3,
                "source_idx": 0,
                "confidence": 0.9,
            },
            {
                "subject": "10001",
                "predicate": "在做",
                "object": "写 Novabot",
                "text": "在做 Novabot",
                "tier": 2,
                "source_idx": 2,
                "confidence": 0.8,
            },
        ],
        "relations": [],
        "gist": "小林在杭州，最近在写 Novabot",
    },
    ensure_ascii=False,
)


class FakeDistillLLM:
    name = "fake-distill"

    def __init__(self, text: str):
        self.text = text
        self.calls: list[dict] = []

    async def chat(self, system="", messages=None, **kw) -> Reply:
        self.calls.append({"system": system, "messages": messages, "kw": kw})
        return Reply(text=self.text)


def remember(mem, text: str) -> int:
    msg = InboundMessage(
        platform="smoke",
        account_id="smoke",
        chat_id="private:10001",
        chat_type=ChatType.PRIVATE,
        sender_qq="10001",
        sender_nick="小林",
        text=text,
        event_at=time.time() - 600,
        recv_at=time.time() - 600,
    )
    return mem.remember(msg)


async def run(use_real: bool) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="novabot-mem-"))
    atexit.register(shutil.rmtree, tmp, True)
    db = Database(tmp / "mem.db")
    init_schema(db)
    settings = Settings(db)
    mem = MemorySystem(db)

    if use_real:
        from app.adapters.llm.factory import build_llm

        llm = build_llm(config.get(), settings)
        if llm is None:
            print("！没配模型：请在 config/local.toml 里写 [llm.xxx]，并设置 llm.default")
            db.close()
            return 2
        print(f"真模型：{llm}")
    else:
        llm = FakeDistillLLM(FAKE_JSON)
        print("假模型（加 --real 用真模型）")

    print("\n=== 写三条原话")
    for text in ("我叫小林，在杭州上班", "最近在写 Novabot", "帮朋友看看服务器的问题"):
        ep = remember(mem, text)
        print(f"  episode={ep}  {text}")

    print("\n=== 跑一轮蒸馏")
    distiller = Distiller(mem, llm, settings)
    stats = await distiller.run_once()
    print(f"  {stats.to_dict()}")

    facts = db.query("SELECT subject_id, text, tier, confidence FROM facts ORDER BY id")
    print(f"\n=== 库里现在有 {len(facts)} 条事实")
    for f in facts:
        print(f"  [{f['subject_id']}] {f['text']}  tier={f['tier']} conf={f['confidence']:.2f}")

    gist = db.query_one("SELECT text FROM gists ORDER BY id DESC LIMIT 1")
    print(f"\n=== 摘要：{gist['text'] if gist else '（无）'}")

    ctx = mem.build_context(
        chat_id="private:10001", sender_qq="10001", account_id="smoke", text="我住哪"
    )
    print("\n=== 拼给模型的记忆上下文")
    for line in ctx.split("\n"):
        print(f"  {line}")

    left = db.scalar("SELECT COUNT(*) FROM episodes WHERE distilled = 0", default=0)
    db.close()

    problems: list[str] = []
    if len(facts) != 2:
        problems.append(f"应有 2 条事实，实际 {len(facts)}")
    if not gist:
        problems.append("没写摘要")
    if left:
        problems.append(f"还有 {left} 条没标已蒸馏")
    if "记得" not in ctx or "杭州" not in ctx:
        problems.append("记忆上下文里没有事实")

    if problems:
        print("\n！冒烟失败：")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("\n冒烟通过")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Novabot 记忆闭环冒烟")
    parser.add_argument("--real", action="store_true", help="用配置里的真模型")
    args = parser.parse_args()
    return asyncio.run(run(args.real))


if __name__ == "__main__":
    sys.exit(main())
