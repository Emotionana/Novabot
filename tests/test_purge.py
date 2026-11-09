"""两阶段删除测试。

跑：python -m tests.test_purge
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Database  # noqa: E402
from app.models import ChatType, InboundMessage  # noqa: E402


def main() -> int:
    import app.params  # noqa: F401
    from app.memory.migrate import init_schema

    tmp = Path(tempfile.mkdtemp(prefix="novabot-purge-"))
    db = Database(tmp / "test.db")
    init_schema(db)

    from app.memory.hippocampus import Hippocampus

    hip = Hippocampus(db, retention_days=7)
    ACC = "default"
    now = time.time()

    print("=== 造三批消息")
    # 20 天前（早就该过期）
    for i in range(5):
        hip.write(
            InboundMessage(
                account_id=ACC, chat_id="group:1", chat_type=ChatType.GROUP,
                group_id="1", sender_qq="111", sender_nick="老王",
                text=f"二十天前的第{i}条", event_at=now - 20 * 86400,
            )
        )
    # 10 天前（刚过期，在宽限期内）
    for i in range(3):
        hip.write(
            InboundMessage(
                account_id=ACC, chat_id="group:1", chat_type=ChatType.GROUP,
                group_id="1", sender_qq="111", sender_nick="老王",
                text=f"十天前的第{i}条", event_at=now - 10 * 86400,
            )
        )
    # 今天（还要留）
    for i in range(4):
        hip.write(
            InboundMessage(
                account_id=ACC, chat_id="group:1", chat_type=ChatType.GROUP,
                group_id="1", sender_qq="111", sender_nick="老王",
                text=f"今天的第{i}条", event_at=now - 3600,
            )
        )

    total = db.scalar("SELECT COUNT(*) FROM episodes")
    print(f"  总共 {total} 条（20天前5 + 10天前3 + 今天4）")
    assert total == 12

    print()
    print("=== 第一阶段：标过期（保留期 7 天）")
    n = hip.expire(days=7)
    print(f"  标记了 {n} 条")
    alive = db.scalar("SELECT COUNT(*) FROM episodes WHERE gone = 0")
    gone = db.scalar("SELECT COUNT(*) FROM episodes WHERE gone = 1")
    print(f"  活着 {alive} 条，标记 {gone} 条，物理上还是 {total} 条")
    assert n == 8, f"应该标 8 条（5+3），实际 {n}"
    assert db.scalar("SELECT COUNT(*) FROM episodes") == 12, "第一阶段不该真删"
    print("  ✓ 只是标记，没真删")

    print()
    print("=== 第二阶段：真删（宽限期 7 天）")
    # 模拟：20 天前那批已经被标记了 10 天，10 天前那批刚标记 2 天
    db.execute(
        "UPDATE episodes SET gone_at = ? WHERE text LIKE '二十天前%'",
        [now - 10 * 86400],
    )
    db.execute(
        "UPDATE episodes SET gone_at = ? WHERE text LIKE '十天前%'",
        [now - 2 * 86400],
    )
    n = hip.purge(grace_days=7)
    print(f"  真删了 {n} 条")
    left = db.scalar("SELECT COUNT(*) FROM episodes")
    print(f"  现在库里 {left} 条")
    assert n == 5, f"应该删 5 条（20天前那批），实际 {n}"
    assert left == 7, f"应该剩 7 条，实际 {left}"

    texts = [r["text"] for r in db.query("SELECT text FROM episodes ORDER BY id")]
    print("  剩下的：")
    for t in texts:
        print(f"    {t}")
    assert not any("二十天前" in t for t in texts), "20 天前的应该删干净"
    assert any("十天前" in t for t in texts), "10 天前的还在宽限期，应保留"

    print()
    print("=== 宽限期过了再删")
    db.execute("UPDATE episodes SET gone_at = ? WHERE gone = 1", [now - 30 * 86400])
    n = hip.purge(grace_days=7)
    print(f"  又删了 {n} 条")
    left = db.scalar("SELECT COUNT(*) FROM episodes")
    print(f"  现在库里 {left} 条（只剩今天的 4 条）")
    assert left == 4
    assert db.scalar("SELECT COUNT(*) FROM episodes WHERE gone = 1") == 0, "不该还有标记的"

    print()
    print("=== 关联的 emotions 会跟着删")
    # 给今天的消息判个情绪
    from app.memory.amygdala import Amygdala

    amy = Amygdala(db)
    eps = hip.recent("group:1", limit=10)
    amy.judge_many(eps)
    before = db.scalar("SELECT COUNT(*) FROM emotions")
    print(f"  情绪记录 {before} 条")

    # 把这些消息标过期再删
    db.execute("UPDATE episodes SET gone = 1, gone_at = ?", [now - 30 * 86400])
    n = hip.purge(grace_days=7)
    after = db.scalar("SELECT COUNT(*) FROM emotions")
    print(f"  删了 {n} 条消息，情绪记录剩 {after} 条")
    assert after == 0, "外键级联应该把情绪也删掉"

    print()
    print("=== purge_all 分批删")
    for i in range(25):
        hip.write(
            InboundMessage(
                account_id=ACC, chat_id="group:1", chat_type=ChatType.GROUP,
                group_id="1", sender_qq="111", text=f"老消息{i}",
                event_at=now - 40 * 86400,
            )
        )
    db.execute("UPDATE episodes SET gone = 1, gone_at = ?", [now - 30 * 86400])
    total = hip.purge_all(grace_days=7, batch=5)
    print(f"  分批删掉 {total} 条")
    assert db.scalar("SELECT COUNT(*) FROM episodes") == 0

    db.close()
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
