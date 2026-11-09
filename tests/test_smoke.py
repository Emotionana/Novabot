"""建表 + 冒烟测试。

跑：python -m tests.test_smoke
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Database  # noqa: E402
from app.models import ChatType, InboundMessage, MsgType  # noqa: E402


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="novabot-test-"))
    db = Database(tmp / "test.db")

    from app.memory import MemorySystem

    mem = MemorySystem(db)
    print("=== 建表完成")

    stats = mem.stats()
    empty = [k for k, v in stats.items() if v != 0]
    print(f"  表数 {len(stats)}，非空的 {len(empty)} 个")
    assert not empty, f"新库应该全是 0：{empty}"

    # 写一条
    msg = InboundMessage(
        platform="qq",
        account_id="default",
        chat_id="group:573560374",
        chat_type=ChatType.GROUP,
        group_id="573560374",
        group_name="测试群",
        sender_qq="10001",
        sender_nick="TestUser",
        sender_card="主人",
        text="今天天气不错",
        msg_type=MsgType.TEXT,
    )
    ep_id = mem.remember(msg)
    print(f"=== 写入 episode id={ep_id}")

    ep = mem.hippocampus.get(ep_id)
    assert ep is not None
    assert ep.text == "今天天气不错"
    assert ep.group_id == "573560374"
    print(f"  读回：{ep.sender_nick} 在 {ep.group_name} 说「{ep.text}」")

    # chats / people 自动建了
    chat = db.query_one("SELECT * FROM chats WHERE chat_id = ?", [msg.chat_id])
    assert chat is not None and chat["title"] == "测试群"
    person = db.query_one("SELECT * FROM people WHERE qq = ?", ["10001"])
    assert person is not None and person["name"] == "TestUser"
    print(f"  自动建了 chat「{chat['title']}」和 person「{person['name']}」")

    # 全文检索
    hits = mem.hippocampus.search(keyword="天气")
    assert len(hits) == 1, f"FTS 应该命中 1 条，实际 {len(hits)}"
    print(f"=== 全文检索「天气」命中 {len(hits)} 条")

    # 再写一条，看看统计涨没涨
    msg2 = InboundMessage(
        account_id="default",
        chat_id="group:573560374",
        chat_type=ChatType.GROUP,
        group_id="573560374",
        sender_qq="10001",
        sender_nick="TestUser",
        text="不过有点热",
    )
    mem.remember(msg2)
    chat = db.query_one("SELECT msg_count FROM chats WHERE chat_id = ?", [msg.chat_id])
    assert chat["msg_count"] == 2, f"msg_count 应该是 2，实际 {chat['msg_count']}"
    print(f"=== 第二条写入，msg_count={chat['msg_count']}")

    # 检索
    items = mem.recall(sender_qq="10001", limit=5)
    print(f"=== recall 拿到 {len(items)} 条")
    for it in items[:3]:
        print(f"  {it.score:.3f}  {it.text[:20]}")

    # 上下文
    ctx = mem.build_context(chat_id=msg.chat_id, sender_qq="10001", account_id="default", text="天气")
    print("=== 上下文：")
    for line in ctx.split("\n"):
        print(f"  {line}")

    # 过期（把保留期设成负的，确保全都过期）
    n = mem.hippocampus.expire(days=-1)
    print(f"=== 过期标记 {n} 条")
    assert n == 2, f"应该标记 2 条，实际 {n}"
    assert mem.hippocampus.count(alive_only=True) == 0

    db.close()
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
