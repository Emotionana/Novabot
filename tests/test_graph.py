"""联想图测试。

跑：python -m tests.test_graph
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
    from app.memory.graph import Graph
    from app.memory.hippocampus import Hippocampus
    from app.memory.migrate import init_schema

    tmp = Path(tempfile.mkdtemp(prefix="novabot-graph-"))
    db = Database(tmp / "test.db")
    init_schema(db)
    g = Graph(db)
    hippo = Hippocampus(db)

    ACC = "default"
    A, B, C = "111", "222", "333"

    print("=== 建节点")
    n_a = g.ensure_node(ACC, "person", A, "阿杰")
    n_b = g.ensure_node(ACC, "person", B, "老林")
    print(f"  阿杰 id={n_a} 老林 id={n_b}")
    assert n_a != n_b

    # 重复 ensure 返回同一个
    n_a2 = g.ensure_node(ACC, "person", A, "阿杰")
    print(f"  再建一次阿杰 id={n_a2}")
    assert n_a2 == n_a

    print()
    print("=== 规则建边：@")
    ep = hippo.get(
        hippo.write(
            InboundMessage(
                account_id=ACC, chat_id="group:1", chat_type=ChatType.GROUP,
                group_id="1", sender_qq=A, sender_nick="阿杰",
                text="老林你看这个", at_list=[B],
            )
        )
    )
    n = g.rule_link(ep)
    print(f"  建了 {n} 条边")
    assert n >= 1

    edges = g.neighbors(n_a)
    print(f"  阿杰的邻居 {len(edges)} 个：{[(e[1], round(e[2],2)) for e in edges]}")
    assert any(e[1] == "mentioned" for e in edges)

    print()
    print("=== 规则建边：回复")
    ep2 = hippo.get(
        hippo.write(
            InboundMessage(
                account_id=ACC, chat_id="group:1", chat_type=ChatType.GROUP,
                group_id="1", sender_qq=B, sender_nick="老林",
                text="嗯,我看到了", reply_to=ep.id,
            )
        )
    )
    g.rule_link(ep2)
    edges_b = g.neighbors(n_b)
    print(f"  老林的邻居：{[(e[1], round(e[2],2)) for e in edges_b]}")
    assert any(e[1] == "replied_to" for e in edges_b)

    print()
    print("=== 模型建边")
    g.model_link(ACC, [
        {"subj": A, "pred": "朋友", "obj": B},
        {"subj": A, "pred": "认识", "obj": C},
        {"subj": B, "pred": "同事", "obj": C},
    ], evidence=[ep.id])
    stats = g.stats()
    print(f"  节点 {stats['nodes']} 个，边 {stats['edges']} 条")
    print(f"  按类型：{stats['by_rel']}")

    print()
    print("=== 重复建边会加权不会重复插")
    before = db.scalar("SELECT weight FROM edges WHERE from_id = ? AND to_id = ? AND rel = 'mentioned'", [n_a, n_b])
    g.link(ACC, A, B, "mentioned", weight=0.6)
    after = db.scalar("SELECT weight FROM edges WHERE from_id = ? AND to_id = ? AND rel = 'mentioned'", [n_a, n_b])
    print(f"  权重 {before:.2f} → {after:.2f}")
    assert after > before, "应该加权"
    cnt = db.scalar("SELECT COUNT(*) FROM edges WHERE from_id = ? AND to_id = ? AND rel = 'mentioned'", [n_a, n_b])
    assert cnt == 1, "不该重复插"

    print()
    print("=== 游走")
    results = g.walk(n_a, depth=2, decay=0.6, min_weight=0.1)
    print(f"  从阿杰出发走了 {len(results)} 个节点：")
    for r in results[:6]:
        print(f"    node={r.node_id} 权重={r.weight:.3f} 深度={r.depth} 通过={r.via}")
    assert len(results) > 0
    # 权重应该是递减的
    weights = [r.weight for r in results]
    assert weights == sorted(weights, reverse=True), "结果应该按权重降序"

    print()
    print("=== 找路径")
    path = g.find_path(n_a, g.ensure_node(ACC, "person", C), max_depth=3)
    print(f"  阿杰 → 某人的路径：{path}")

    print()
    print("=== 批量 vs 单个：性能")
    # 建一批边
    for i in range(30):
        g.link(ACC, A, f"x{i}", "test", weight=0.5)
    t0 = time.time()
    for _ in range(3):
        g.walk(n_a, depth=2)
    t1 = time.time()
    print(f"  walk 深度 2 跑 3 次：{(t1-t0)*1000:.1f} ms（含 30 个额外邻居）")

    print()
    print("=== 修剪")
    # 造一条弱边
    g.link(ACC, A, "weak", "test", weight=0.05)
    db.execute("UPDATE edges SET updated_at = ? WHERE weight < 0.1", [time.time() - 100 * 86400])
    n = g.prune(min_weight=0.1, days=90)
    print(f"  清掉 {n} 条弱边")

    print()
    print("=== 孤立节点")
    g.ensure_node(ACC, "person", "lonely", "孤立的")
    n2 = g.prune_nodes()
    print(f"  清掉 {n2} 个孤立节点")
    assert g.get_node(ACC, "person", "lonely") is None

    db.close()
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
