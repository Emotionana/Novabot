"""AI 秘书最小闭环冒烟：时间 → 笔记 → 提醒 → 到点发送 → 审计。

不联网、不起服务。
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
from app.db import Database  # noqa: E402
from app.memory.migrate import init_schema  # noqa: E402
from app.tools import REGISTRY, ToolContext  # noqa: E402
from app.tools.reminders import ReminderService  # noqa: E402

TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
NOW = datetime(2026, 11, 7, 20, 0, tzinfo=TZ).timestamp()


class FakeManager:
    def __init__(self):
        self.sent = []

    async def send_to_chat(self, acc_id, chat_id, text):
        self.sent.append((acc_id, chat_id, text))
        return True


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="novabot-tools-"))
    atexit.register(shutil.rmtree, tmp, True)
    db = Database(tmp / "tools.db")
    init_schema(db)
    ctx = ToolContext(
        db=db, account_id="a", chat_id="private:111", person_qq="111",
        is_owner=True, now=NOW, tz=TZ,
    )

    print("=== 现在几点")
    print(" ", REGISTRY.execute("current_time", {}, ctx))

    print("=== 记笔记")
    print(" ", REGISTRY.execute("add_note", {"body": "周五交周报"}, ctx))
    print(" ", REGISTRY.execute("search_notes", {"query": "周报"}, ctx))

    print("=== 加提醒")
    print(" ", REGISTRY.execute("add_reminder", {"text": "喝水", "when": "10分钟后"}, ctx))
    print(" ", REGISTRY.execute("list_reminders", {}, ctx))

    print("=== 到点发送")
    manager = FakeManager()
    svc = ReminderService(db, manager)
    out = __import__("asyncio").run(svc.check_due(now=NOW + 900))
    print(" ", out)
    print(" ", manager.sent)

    print("=== 审计")
    for row in db.query("SELECT tool, ok, duration_ms FROM tool_calls ORDER BY id"):
        print(f"  {row['tool']:<14} ok={row['ok']} {row['duration_ms']}ms")

    db.close()
    if not manager.sent or "喝水" not in manager.sent[0][2]:
        print("！提醒没发出去")
        return 1
    print("\nAI 秘书冒烟通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
