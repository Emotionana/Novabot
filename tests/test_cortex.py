"""新皮层的测试。

跑：python -m tests.test_cortex
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Database  # noqa: E402
from app.models import Fact  # noqa: E402


def main() -> int:
    import app.params  # noqa: F401
    from app.memory.cortex import Cortex
    from app.memory.migrate import init_schema

    tmp = Path(tempfile.mkdtemp(prefix="novabot-cortex-"))
    db = Database(tmp / "test.db")
    init_schema(db)
    cortex = Cortex(db)

    ACC = "default"
    QQ = "10001"
    now = time.time()

    print("=== 写第一条事实")
    f1 = Fact(
        account_id=ACC, subject_id=QQ, predicate="住在", object="广州",
        text="住在广州", tier=2, valid_from=now - 86400 * 100,
    )
    id1, how = cortex.write_fact(f1)
    print(f"  id={id1} 处理方式={how}")
    assert how == "inserted"

    print()
    print("=== 同一件事再说一遍 → duplicate")
    f2 = Fact(
        account_id=ACC, subject_id=QQ, predicate="住在", object="广州",
        text="住在广州", tier=2, valid_from=now,
    )
    id2, how = cortex.write_fact(f2)
    print(f"  id={id2} 处理方式={how}")
    assert how == "duplicate", f"应该是 duplicate，实际 {how}"
    assert id2 == id1, "重复的应该返回原 id"

    # 检查 hits 涨了
    got = cortex.get_fact(id1)
    print(f"  hits={got.hits}")
    assert got.hits >= 2

    print()
    print("=== 换说法说同一件事 → 还是 duplicate")
    f3 = Fact(
        account_id=ACC, subject_id=QQ, predicate="住在", object="广州",
        text="广州住着", tier=2, valid_from=now,
    )
    _, how = cortex.write_fact(f3)
    print(f"  处理方式={how}")
    assert how == "duplicate"

    print()
    print("=== 搬家了 → new_wins，旧的标终止")
    # 模拟：100 天前记的「广州」，今天听说搬了
    # recorded_at 差很远，所以判 new_wins
    f4 = Fact(
        account_id=ACC, subject_id=QQ, predicate="住在", object="杭州",
        text="搬到杭州了", tier=2,
        valid_from=now, recorded_at=now + 86401,
    )
    id4, how = cortex.write_fact(f4)
    print(f"  id={id4} 处理方式={how}")
    assert how == "superseded", f"应该是 superseded，实际 {how}"

    old = cortex.get_fact(id1)
    print(f"  旧事实 valid_until={old.valid_until} superseded_by={old.superseded_by}")
    assert old.valid_until is not None, "旧事实应该标终止"
    assert old.superseded_by == id4

    print()
    print("=== 查「现在住哪」→ 只有杭州")
    live = cortex.facts_of(QQ, ACC, live_only=True)
    print(f"  现在成立的事实 {len(live)} 条：{[f.text for f in live]}")
    assert len(live) == 1 and "杭州" in live[0].text

    print()
    print("=== 查「以前住哪」→ 只有广州")
    all_f = cortex.facts_of(QQ, ACC, live_only=False)
    dead = [f for f in all_f if f.valid_until is not None]
    print(f"  已终止的事实 {len(dead)} 条：{[f.text for f in dead]}")
    assert len(dead) == 1 and "广州" in dead[0].text

    print()
    print("=== 查某个时间点（100 天前）→ 广州")
    past = cortex.facts_at(QQ, now - 86400 * 90, ACC)
    print(f"  那时成立 {len(past)} 条：{[f.text for f in past]}")
    assert any("广州" in f.text for f in past), "那时候应该住广州"

    print()
    print("=== 变化史")
    hist = cortex.history_of(QQ, "住在", ACC)
    print(f"  「住在」的变化史 {len(hist)} 条：")
    for h in hist:
        when = "现在" if h.valid_until is None else "以前"
        print(f"    {when}：{h.text}")
    assert len(hist) == 2

    print()
    print("=== tier 0 置顶")
    f5 = Fact(
        account_id=ACC, subject_id=QQ, predicate="名字", object="TestUser",
        text="叫 TestUser", tier=0, valid_from=now - 86400 * 500,
    )
    id5, _ = cortex.write_fact(f5)
    # 再加一条普通的
    f6 = Fact(
        account_id=ACC, subject_id=QQ, predicate="喜欢", object="原神",
        text="喜欢原神", tier=2, valid_from=now,
    )
    cortex.write_fact(f6)

    recalled = cortex.recall(QQ, ACC, limit=5)
    print(f"  recall 顺序：{[f.text for f in recalled]}")
    assert recalled[0].tier == 0, f"tier 0 应该排第一，实际 {recalled[0].text}"

    print()
    print("=== 打分对比")
    for f in recalled:
        s = cortex.score(f, now)
        shown = "置顶" if s > 1e8 else f"{s:.3f}"
        print(f"  {f.text:14s} tier={f.tier} → {shown}")

    print()
    print("=== 多账号隔离")
    f7 = Fact(
        account_id="acc-other", subject_id=QQ, predicate="住在", object="深圳",
        text="住在深圳（另一个号记的）", tier=2, valid_from=now,
    )
    cortex.write_fact(f7)
    a_live = cortex.facts_of(QQ, ACC, live_only=True)
    b_live = cortex.facts_of(QQ, "acc-other", live_only=True)
    print(f"  默认号看到：{[f.text for f in a_live]}")
    print(f"  另一个号看到：{[f.text for f in b_live]}")
    assert not any("深圳" in f.text for f in a_live), "不该串到另一个号"
    assert any("深圳" in f.text for f in b_live)

    print()
    print("=== 蒸馏落库")
    from app.memory.hippocampus import Hippocampus
    from app.models import ChatType, InboundMessage

    hippo = Hippocampus(db)
    ep_ids = []
    for text in ("我最近在学吉他", "练了两周了", "还挺难的"):
        ep_ids.append(
            hippo.write(
                InboundMessage(
                    account_id=ACC, chat_id="group:1", chat_type=ChatType.GROUP,
                    group_id="1", sender_qq=QQ, sender_nick="TestUser", text=text,
                )
            )
        )
    eps = hippo.get_many(ep_ids)
    print(f"  写了 {len(eps)} 条 episode")

    result = {
        "facts": [
            {"subject": QQ, "predicate": "在做", "object": "学吉他",
             "text": "在学吉他", "tier": 2, "source_idx": 0},
        ],
        "gist": "在学吉他，练了两周",
    }
    stats = cortex.apply_distill(ACC, "group:1", eps, result)
    print(f"  落库统计：{stats}")
    assert stats["facts"] == 1 and stats["gists"] == 1

    # 验证 valid_from 取的是第 0 条的时间，不是最后一条
    new_fact = cortex.facts_of(QQ, ACC, live_only=True, limit=10)
    guitar = next((f for f in new_fact if "吉他" in f.text), None)
    assert guitar is not None
    print(f"  吉他那条 valid_from：{guitar.valid_from:.0f}")
    print(f"  第 1 条 episode：{eps[0].event_at:.0f}  最后一条：{eps[-1].event_at:.0f}")
    assert abs(guitar.valid_from - eps[0].event_at) < 1, "应该取第 0 条的时间"
    print("  ✓ 没取最后一条")

    db.close()
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
