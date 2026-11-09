"""联想图。

把人和事连成网，检索时能顺着想到别的。

三种建边：
  规则（同步，最便宜）—— @、回复、共现
  模型（蒸馏时）—— 三元组直接落边
  向量（异步）—— 相似度超过阈值就连

游走要注意性能：每层每节点一次 SQL，depth=2 加 20 个邻居就是 400 次查询。
所以改成批量查，并且给时间预算。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from ..db import Database
from ..models import Episode

log = logging.getLogger(__name__)


@dataclass
class Node:
    id: int = 0
    account_id: str = ""
    kind: str = "person"      # person / group / fact / episode / topic
    ref: str = ""
    label: str = ""
    weight: float = 1.0


@dataclass
class WalkResult:
    """游走结果。"""

    node_id: int = 0
    weight: float = 0.0
    depth: int = 0
    via: str = ""             # 通过哪条边过来的


class Graph:
    def __init__(self, db: Database, settings=None):
        self.db = db
        self.settings = settings

    # ==================================================================
    # 节点
    # ==================================================================

    def ensure_node(
        self, account_id: str, kind: str, ref: str, label: str = "", weight: float = 1.0
    ) -> int:
        """确保节点存在，返回 id。"""
        row = self.db.query_one(
            "SELECT id FROM nodes WHERE account_id = ? AND kind = ? AND ref = ?",
            [account_id, kind, ref],
        )
        if row:
            if label:
                self.db.execute("UPDATE nodes SET label = ? WHERE id = ?", [label, row["id"]])
            return row["id"]

        return self.db.insert(
            "INSERT INTO nodes (account_id, kind, ref, label, weight, created_at) VALUES (?,?,?,?,?,?)",
            [account_id, kind, ref, label, weight, time.time()],
        )

    def get_node(self, account_id: str, kind: str, ref: str) -> Node | None:
        row = self.db.query_one(
            "SELECT * FROM nodes WHERE account_id = ? AND kind = ? AND ref = ?",
            [account_id, kind, ref],
        )
        if row is None:
            return None
        return Node(
            id=row["id"], account_id=row["account_id"], kind=row["kind"],
            ref=row["ref"], label=row["label"] or "", weight=row["weight"] or 1.0,
        )

    # ==================================================================
    # 建边
    # ==================================================================

    def link(
        self,
        account_id: str,
        from_ref: str,
        to_ref: str,
        rel: str,
        *,
        from_kind: str = "person",
        to_kind: str = "person",
        weight: float = 0.5,
        evidence: list[int] | None = None,
    ) -> None:
        """建边。已存在就加权。"""
        if from_ref == to_ref:
            return

        a = self.ensure_node(account_id, from_kind, from_ref)
        b = self.ensure_node(account_id, to_kind, to_ref)
        now = time.time()

        row = self.db.query_one(
            "SELECT id, weight, evidence FROM edges WHERE from_id = ? AND to_id = ? AND rel = ?",
            [a, b, rel],
        )
        if row:
            ev = json.loads(row["evidence"] or "[]")
            for e in evidence or []:
                if e not in ev:
                    ev.append(e)
            # 权重往上加，但封顶 1.0
            new_w = min(row["weight"] + weight * 0.3, 1.0)
            self.db.execute(
                "UPDATE edges SET weight = ?, evidence = ?, updated_at = ? WHERE id = ?",
                [new_w, json.dumps(ev[-50:]), now, row["id"]],
            )
        else:
            self.db.execute(
                "INSERT INTO edges (account_id, from_id, to_id, rel, weight, evidence, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [account_id, a, b, rel, weight, json.dumps(evidence or [])[:1000], now, now],
            )

    def rule_link(self, ep: Episode) -> int:
        """规则建边：@、回复、共现。返回建了几条。"""
        n = 0
        account_id = ep.account_id
        me = ep.sender_qq
        if not me:
            return 0

        # @ 关系
        try:
            at_list = json.loads(ep.at_list or "[]")
        except json.JSONDecodeError:
            at_list = []
        for at_qq in at_list:
            if at_qq and at_qq != me:
                self.link(account_id, me, at_qq, "mentioned", weight=0.6, evidence=[ep.id])
                n += 1

        # 回复关系
        if ep.reply_to:
            orig = self.db.query_one(
                "SELECT sender_qq FROM episodes WHERE id = ?", [ep.reply_to]
            )
            if orig and orig["sender_qq"] and orig["sender_qq"] != me:
                self.link(
                    account_id, me, orig["sender_qq"], "replied_to",
                    weight=0.7, evidence=[ep.id],
                )
                n += 1

        # 消息挂到说话人节点上
        if ep.text:
            self.link(
                account_id, me, str(ep.id), "said",
                from_kind="person", to_kind="episode", weight=0.5, evidence=[ep.id],
            )
            n += 1

        return n

    def model_link(
        self, account_id: str, relations: list[dict], evidence: list[int] | None = None
    ) -> int:
        """模型建边：蒸馏输出的三元组。"""
        n = 0
        for r in relations:
            subj = str(r.get("subj") or "")
            obj = str(r.get("obj") or "")
            pred = r.get("pred") or "related"
            if not subj or not obj:
                continue
            self.link(
                account_id, subj, obj, pred,
                weight=0.8, evidence=evidence,
            )
            n += 1
        return n

    def vector_link(self, account_id: str, kind: str, ref_a: str, ref_b: str, sim: float) -> None:
        """向量建边：两条记忆相似。"""
        if sim < 0.85:
            return
        self.link(
            account_id, ref_a, ref_b, "similar_to",
            from_kind=kind, to_kind=kind, weight=sim,
        )

    # ==================================================================
    # 查询
    # ==================================================================

    def neighbors(
        self, node_id: int, rel: str | None = None, limit: int = 20
    ) -> list[tuple[int, str, float]]:
        """直接邻居：(node_id, rel, weight)。"""
        if rel:
            rows = self.db.query(
                "SELECT to_id, rel, weight FROM edges WHERE from_id = ? AND rel = ? "
                "ORDER BY weight DESC LIMIT ?",
                [node_id, rel, limit],
            )
        else:
            rows = self.db.query(
                "SELECT to_id, rel, weight FROM edges WHERE from_id = ? "
                "ORDER BY weight DESC LIMIT ?",
                [node_id, limit],
            )
        return [(r["to_id"], r["rel"], r["weight"]) for r in rows]

    def _batch_neighbors(
        self, node_ids: list[int], limit_each: int = 10
    ) -> dict[int, list[tuple[int, str, float]]]:
        """批量查邻居。一次 SQL 搞定一层，避免 N+1。"""
        if not node_ids:
            return {}
        marks = ",".join("?" * len(node_ids))
        rows = self.db.query(
            f"""
            SELECT from_id, to_id, rel, weight FROM edges
            WHERE from_id IN ({marks})
            ORDER BY from_id, weight DESC
            """,
            node_ids,
        )
        out: dict[int, list[tuple[int, str, float]]] = {}
        for r in rows:
            bucket = out.setdefault(r["from_id"], [])
            if len(bucket) < limit_each:
                bucket.append((r["to_id"], r["rel"], r["weight"]))
        return out

    def walk(
        self,
        seed_node: int,
        *,
        depth: int = 2,
        decay: float = 0.6,
        min_weight: float = 0.15,
        limit: int = 20,
        budget_ms: int = 80,
    ) -> list[WalkResult]:
        """从种子出发走 depth 步。

        每层批量查，且有时间预算——超了只返回已走完的层。
        """
        start = time.time()
        visited: dict[int, WalkResult] = {seed_node: WalkResult(seed_node, 1.0, 0, "seed")}
        frontier = [(seed_node, 1.0)]
        done_depth = 0

        for d in range(1, depth + 1):
            if not frontier:
                break
            if (time.time() - start) * 1000 > budget_ms:
                log.debug("walk 超预算，走了 %d 层", done_depth)
                break

            batch = self._batch_neighbors([nid for nid, _ in frontier])
            nxt = []
            for node_id, w in frontier:
                for to_id, rel, edge_w in batch.get(node_id, []):
                    new_w = w * edge_w * decay
                    if new_w < min_weight:
                        continue
                    if to_id not in visited or visited[to_id].weight < new_w:
                        visited[to_id] = WalkResult(to_id, new_w, d, rel)
                        nxt.append((to_id, new_w))
            frontier = nxt
            done_depth = d

        # 去掉种子自身
        results = [r for nid, r in visited.items() if nid != seed_node]
        results.sort(key=lambda r: -r.weight)
        return results[:limit]

    def find_path(self, from_node: int, to_node: int, max_depth: int = 3) -> list[int] | None:
        """两点之间有没有路。BFS。"""
        if from_node == to_node:
            return [from_node]

        prev: dict[int, int] = {from_node: -1}
        queue = [from_node]

        for _ in range(max_depth):
            if not queue:
                break
            batch = self._batch_neighbors(queue)
            nxt = []
            for node_id in queue:
                for to_id, _, _ in batch.get(node_id, []):
                    if to_id in prev:
                        continue
                    prev[to_id] = node_id
                    if to_id == to_node:
                        # 回溯
                        path = [to_node]
                        cur = to_node
                        while prev[cur] != -1:
                            cur = prev[cur]
                            path.append(cur)
                        return list(reversed(path))
                    nxt.append(to_id)
            queue = nxt

        return None

    # ==================================================================
    # 维护
    # ==================================================================

    def prune(self, *, min_weight: float = 0.1, days: int = 90) -> int:
        """清掉长期不用的弱边。"""
        cutoff = time.time() - days * 86400
        return self.db.execute(
            "DELETE FROM edges WHERE weight < ? AND updated_at < ?",
            [min_weight, cutoff],
        )

    def prune_nodes(self, *, min_edges: int = 1) -> int:
        """清掉孤立节点。

        注意：edges 为空时 NOT IN (空集) 恒为真，会把所有节点删光。
        所以先判一下有没有边。
        """
        if not self.db.scalar("SELECT COUNT(*) FROM edges", default=0):
            return 0
        return self.db.execute(
            """
            DELETE FROM nodes WHERE id NOT IN (
              SELECT from_id FROM edges UNION SELECT to_id FROM edges
            )
            """
        )

    def stats(self) -> dict:
        return {
            "nodes": self.db.scalar("SELECT COUNT(*) FROM nodes", default=0),
            "edges": self.db.scalar("SELECT COUNT(*) FROM edges", default=0),
            "by_rel": {
                r["rel"]: r["n"]
                for r in self.db.query(
                    "SELECT rel, COUNT(*) AS n FROM edges GROUP BY rel ORDER BY n DESC"
                )
            },
        }
