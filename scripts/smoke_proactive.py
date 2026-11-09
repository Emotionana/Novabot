"""主动私聊冒烟：习惯 → 候选 → 生成 → 发送 → 回复 → 第二天再发。

不联网、不起服务，用临时库 + 假模型 + 假发送，验证整条决策链。

用法：
    python -m scripts.smoke_proactive
"""

from __future__ import annotations

import atexit
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.params  # noqa: E402,F401
from app.adapters.llm.base import Reply  # noqa: E402
from app.db import Database  # noqa: E402
from app.memory.migrate import init_schema  # noqa: E402
from app.memory.system import MemorySystem  # noqa: E402
from app.proactive import ProactiveService  # noqa: E402
from app.settings import Settings  # noqa: E402

TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
NOW = datetime(2026, 11, 7, 20, 0, tzinfo=TZ).timestamp()
ACC = "acc-pro"
QQ = "10001"


class FakeLLM:
    name = "fake"

    def __init__(self, text: str):
        self.text = text

    async def chat(self, **kwargs):
        return Reply(text=self.text)


class FakeManager:
    def __init__(self, text: str):
        self.sent: list[tuple[str, str, str]] = []
        self.llm = FakeLLM(text)

    def online_accounts(self):
        return [ACC]

    def llm_for(self, account_id):
        return self.llm

    async def send_private(self, account_id, qq, text):
        self.sent.append((account_id, qq, text))
        return True


def seed(db: Database) -> None:
    first = NOW - 10 * 86400
    last = NOW - 24 * 3600
    db.execute(
        "INSERT INTO people (qq, account_id, name, msg_count, first_seen, last_seen, is_owner) "
        "VALUES (?,?,?,?,?,?,?)",
        [QQ, ACC, "老林", 40, first, last, 0],
    )
    texts = ["今天有点累", "晚饭吃了", "回头聊", "你上次说的那个事后来怎么样了？"]
    for i in range(40):
        ts = last - (i % 10) * 86400
        db.execute(
            "INSERT INTO episodes (account_id, chat_id, chat_type, sender_qq, sender_nick, "
            "text, event_at, recv_at) VALUES (?,?,?,?,?,?,?,?)",
            [ACC, f"private:{QQ}", "private", QQ, "老林", texts[i % len(texts)], ts, ts],
        )
    db.execute(
        "INSERT INTO facts (account_id, subject_type, subject_id, predicate, object, text, "
        "recorded_at) VALUES (?,?,?,?,?,?,?)",
        [ACC, "person", QQ, "喜欢", "冰美式", "他喜欢喝冰美式", NOW - 2 * 86400],
    )


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="novabot-proactive-"))
    atexit.register(shutil.rmtree, tmp, True)
    db = Database(tmp / "proactive.db")
    init_schema(db)
    seed(db)

    settings = Settings(db)
    for key, value in {
        "proactive.enabled": True,
        "proactive.min_messages": 30,
        "proactive.min_days": 0,
        "proactive.min_silence_hours": 6.0,
        "proactive.max_per_day": 1,
        "proactive.global_max_per_day": 5,
        "proactive.score_threshold": 0.0,
        "proactive.quiet_start": 23,
        "proactive.quiet_end": 8,
        "proactive.wait_reply_hours": 24,
        "proactive.interval_hours": 12,
        "proactive.backoff_base_days": 1,
        "proactive.max_chars": 60,
        "proactive.dry_run": False,
    }.items():
        settings.set(key, value)

    memory = MemorySystem(db)
    manager = FakeManager("这个点你还没睡？")
    svc = ProactiveService(db, memory, None, settings, manager=manager)

    print("=== 第一次 tick（20:00，沉默 24h）")
    import asyncio

    decisions = asyncio.run(svc.tick(now=NOW))
    for d in decisions:
        print(
            f"  {d['status']:8s} {d.get('name', '')} score={d.get('score', 0):.2f} "
            f"{d.get('reason', '')}"
        )
        if d.get("message"):
            print(f"           → {d['message']}")
    if decisions[0]["status"] != "sent":
        print("！第一次没发出去")
        return 1

    print("\n=== 第二次 tick（同一分钟）")
    again = asyncio.run(svc.tick(now=NOW + 60))
    print(f"  {again[0]['status']}：{again[0].get('reason', '')}")
    if again[0]["status"] != "skipped":
        print("！每日上限没拦住")
        return 1

    print("\n=== 用户回复")
    ok = svc.on_user_message(ACC, QQ, "刚看到，你怎么也没睡", at=NOW + 300)
    print(f"  标记 replied={ok}")
    row = db.query_one("SELECT status, reply_text FROM proactive_log ORDER BY id DESC LIMIT 1")
    print(f"  log: {row['status']} / {row['reply_text']}")
    if not ok or row["status"] != "replied":
        print("！回复没记上")
        return 1

    print("\n=== 第二天 20:00")
    manager.llm.text = "第二天了，今天忙吗"
    next_day = asyncio.run(svc.tick(now=NOW + 86400))
    print(f"  {next_day[0]['status']}：{next_day[0].get('message', '')}")
    if next_day[0]["status"] != "sent":
        print("！第二天没发出去")
        return 1

    print("\n=== 发送记录")
    for row in db.query(
        "SELECT person_name, sent_at, message, status, score FROM proactive_log ORDER BY id"
    ):
        print(
            f"  {row['person_name']} {datetime.fromtimestamp(row['sent_at'], TZ):%m-%d %H:%M} "
            f"[{row['status']}] {row['message']} ({row['score']:.2f})"
        )

    state = db.query_one(
        "SELECT * FROM proactive_state WHERE account_id=? AND person_qq=?", [ACC, QQ]
    )
    print(
        f"\n状态：last_sent={datetime.fromtimestamp(state['last_sent_at'], TZ):%m-%d %H:%M} "
        f"streak={state['ignored_streak']} daily={state['daily_count']}"
    )

    db.close()
    print("\n主动私聊冒烟通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
