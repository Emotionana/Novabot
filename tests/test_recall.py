"""检索调度测试。

跑：python -m tests.test_recall
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Database  # noqa: E402
from app.models import ChatType, Fact, InboundMessage  # noqa: E402


def main() -> int:
    import app.params  # noqa: F401
    from app.memory.cortex import Cortex
    from app.memory.graph import Graph
    from app.memory.hippocampus import Hippocampus
    from app.memory.migrate import init_schema
    from app.memory.recall import Recall, classify_intent

    tmp = Path(tempfile.mkdtemp(prefix="novabot-recall-"))
    db = Database(tmp / "test.db")
    init_schema(db)

    hippo = Hippocampus(db)
    cortex = Cortex(db)
    graph = Graph(db)
    rec = Recall(db, hippo, cortex, graph)

    ACC = "default"
    QQ = "10001"
    now = time.time()

    print("=== 意图判断")
    cases = [
        ("他是谁", {"profile"}),
        ("最近怎么样", {"recent", "fact"}),
        ("上次说的那个", {"episode"}),
        ("他答应过我什么", {"promise"}),
        ("你以前对我什么态度", {"self", "history", "fact"}),
    ]
    for text, expect in cases:
        got = classify_intent(text)
        mark = "✓" if expect <= got else "?"
        print(f"  {mark} {text:18s} → {sorted(got)}")
    assert classify_intent("随便聊聊") == {"profile", "recent", "fact"}, "默认三路"

    print()
    print("=== 造数据")
    # 人物
    db.execute(
        "INSERT INTO people (qq, account_id, name, style, persona, handle, msg_count, first_seen, last_seen) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [QQ, ACC, "TestUser", "说话短", "随和", "直接答", 20, now - 86400 * 60, now],
    )
    # 原话
    for text in ("今天加班到十点", "项目又延期了", "终于修好那个bug", "想早点睡"):
        hippo.write(
            InboundMessage(
                account_id=ACC, chat_id="group:1", chat_type=ChatType.GROUP,
                group_id="1", sender_qq=QQ, sender_nick="TestUser",
                text=text, event_at=now - 3600,
            )
        )
    # 事实
    cortex.write_fact(Fact(
        account_id=ACC, subject_id=QQ, predicate="在做", object="后端",
        text="做后端开发", tier=2, valid_from=now - 86400 * 30,
    ))
    cortex.write_fact(Fact(
        account_id=ACC, subject_id=QQ, predicate="喜欢", object="原神",
        text="喜欢玩原神", tier=3, valid_from=now - 86400 * 10,
    ))
    cortex.write_fact(Fact(
        account_id=ACC, subject_id=QQ, predicate="名字", object="TestUser",
        text="叫 TestUser", tier=0, valid_from=now - 86400 * 100,
    ))
    # 一条历史（搬家）
    cortex.write_fact(Fact(
        account_id=ACC, subject_id=QQ, predicate="住在", object="广州",
        text="住在广州", tier=2,
        valid_from=now - 86400 * 200, recorded_at=now - 86400 * 200,
    ))
    cortex.write_fact(Fact(
        account_id=ACC, subject_id=QQ, predicate="住在", object="杭州",
        text="搬到杭州", tier=2, valid_from=now - 86400 * 20, recorded_at=now,
    ))
    # 图
    node = graph.ensure_node(ACC, "person", QQ, "TestUser")
    graph.link(ACC, QQ, "others", "朋友", weight=0.8)
    graph.ensure_node(ACC, "person", "others", "小林")

    print("  人物 1 个，原话 4 条，事实 5 条")

    print()
    print("=== 一级：普通消息只带画像")
    ctx = rec.build_context(
        account_id=ACC, chat_id="group:1", sender_qq=QQ, text="嗯", max_chars=300
    )
    print(f"  route={sorted(ctx.routes)} chars={ctx.chars}")
    for level, content in ctx.parts:
        print(f"    [{level}] {content[:60]}")
    assert ctx.parts[0][0] == "profile", "画像必须第一"

    print()
    print("=== 二级：问近况带原话+事实")
    ctx2 = rec.build_context(
        account_id=ACC, chat_id="group:1", sender_qq=QQ, text="最近怎么样", max_chars=400
    )
    print(f"  route={sorted(ctx2.routes)} chars={ctx2.chars}")
    for level, content in ctx2.parts:
        print(f"    [{level}] {content[:60]}")
    assert any(p[0] == "recent" for p in ctx2.parts)

    print()
    print("=== 三级半：问历史带变化")
    ctx3 = rec.build_context(
        account_id=ACC, chat_id="group:1", sender_qq=QQ, text="他以前住哪", max_chars=500
    )
    print(f"  route={sorted(ctx3.routes)} chars={ctx3.chars}")
    for level, content in ctx3.parts:
        print(f"    [{level}] {content[:70]}")
    assert any(p[0] == "history" for p in ctx3.parts), "问历史应该带变化"

    print()
    print("=== 层级顺序固定")
    levels = [p[0] for p in ctx3.parts]
    print(f"  实际顺序：{levels}")
    order = {"profile": 0, "recent": 1, "fact": 2, "history": 3, "related": 4}
    assert levels == sorted(levels, key=lambda x: order.get(x, 9)), "顺序应该按优先级"

    print()
    print("=== 裁剪")
    ctx4 = rec.build_context(
        account_id=ACC, chat_id="group:1", sender_qq=QQ, text="最近怎么样他以前住哪", max_chars=80
    )
    print(f"  上限 80 字，实际 {ctx4.chars} 字")
    print(f"  {ctx4.text[:100]}")
    assert ctx4.chars <= 85, f"超了：{ctx4.chars}"

    print()
    print("=== 查不到人时返回空")
    ctx5 = rec.build_context(
        account_id=ACC, chat_id="group:1", sender_qq="999999", text="最近怎么样"
    )
    print(f"  chars={ctx5.chars}")
    assert ctx5.chars == 0

    print()
    print("=== 通用检索")
    items = rec.search("加班", account_id=ACC, sender_qq=QQ, limit=5)
    print(f"  「加班」找到 {len(items)} 条：")
    for it in items[:3]:
        print(f"    {it.kind:8s} {it.score:.3f}  {it.text[:30]}")

    print()
    print("=== 调试解释")
    ex = rec.explain(account_id=ACC, chat_id="group:1", sender_qq=QQ, text="最近怎么样")
    print(f"  route={sorted(ex.routes)}")
    print(f"  画像：{ex.profile[:40]}")
    print(f"  最近：{len(ex.recent)} 条")
    print("  事实打分：")
    for text, score in ex.facts[:5]:
        shown = "置顶" if score > 1e8 else f"{score:.3f}"
        print(f"    {text:16s} {shown}")
    print(f"  相关：{ex.related}")
    print(f"  总计：{ex.total_chars} 字")

    db.close()
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
