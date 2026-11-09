"""检索调度（前额叶）。

一次查询，决定去哪几个模块取、取多少、怎么拼。

分四级，大部分消息只要一级：
  一级 人物画像（几乎每次都要）
  二级 最近的话（问近况时）
  三级 事实（问事情时）
  四级 联想扩展（前面都很空时）
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

from ..db import Database
from ..models import Fact, ScoredItem

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 意图
# ---------------------------------------------------------------------------

INTENT_PATTERNS = {
    "profile": r"(他是谁|什么人|什么性格|怎么样的人|怎么应付|这个人)",
    "recent": r"(最近|这几天|今天|刚才|现在在|这几天)",
    "episode": r"(上次|之前|以前|那次|说过|提过|聊过)",
    "promise": r"(答应|说好|约好|承诺|保证|约定)",
    "relation": r"(跟谁|和谁|什么关系|认识|认识吗)",
    "self": r"(你|你自己|你怎么想|你喜欢|你觉得)",
    "history": r"(以前|过去|变过|改过|原来)",
}

# 默认取这几路
DEFAULT_ROUTES = {"profile", "recent", "fact"}


def classify_intent(text: str) -> set[str]:
    """判这次该取哪几路。"""
    routes: set[str] = set()
    for name, pat in INTENT_PATTERNS.items():
        if re.search(pat, text or ""):
            routes.add(name)

    if not routes:
        return set(DEFAULT_ROUTES)

    # 问近况也顺带带事实
    if "recent" in routes:
        routes.add("fact")
    # 问历史一定要事实
    if "history" in routes:
        routes.add("fact")

    return routes


# ---------------------------------------------------------------------------
# 结果
# ---------------------------------------------------------------------------


@dataclass
class Context:
    """拼好的上下文。"""

    text: str = ""
    chars: int = 0
    routes: set[str] = field(default_factory=set)
    parts: list[tuple[str, str]] = field(default_factory=list)   # (级别, 内容)
    facts: list[Fact] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.text)


@dataclass
class Explanation:
    """调试用：为什么取了这些。"""

    routes: set[str] = field(default_factory=set)
    profile: str = ""
    recent: list[str] = field(default_factory=list)
    facts: list[tuple[str, float]] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    total_chars: int = 0


class Recall:
    def __init__(
        self, db: Database, hippo, cortex, graph=None, amygdala=None,
        settings=None, profiles=None,
    ):
        self.db = db
        self.hippo = hippo
        self.cortex = cortex
        self.graph = graph
        self.amygdala = amygdala
        self.settings = settings
        self.profiles = profiles
        self._cfg = None

    @property
    def cfg(self):
        if self._cfg is None:
            from .. import config

            self._cfg = config.get().memory
        return self._cfg

    # ==================================================================
    # 主入口
    # ==================================================================

    def build_context(
        self,
        *,
        account_id: str,
        chat_id: str,
        sender_qq: str,
        text: str = "",
        max_chars: int | None = None,
        mood: str | None = None,
        exclude_id: int | None = None,
        allowed_chat_ids: set[str] | None = None,
        profile_detail: bool = True,
        allow_related: bool = True,
        allow_unknown_facts: bool = False,
    ) -> Context:
        """拼一段给模型的记忆上下文。"""
        max_chars = max_chars or self.cfg.recall_chars
        routes = classify_intent(text)
        ctx = Context(routes=routes)

        # 一级：人物画像（永远第一）
        profile = self._profile_line(sender_qq, account_id, detail=profile_detail)
        if profile:
            ctx.parts.append(("profile", profile))

        # 二级：最近的话
        if "recent" in routes or "episode" in routes:
            recent = self._recent_line(
                sender_qq, account_id, days=self.cfg.recent_days, exclude_id=exclude_id,
                chat_id=chat_id,
            )
            if recent:
                ctx.parts.append(("recent", recent))

        # 三级：事实（预算够才带）
        used = self._used_chars(ctx.parts)
        if used < max_chars * 0.6:
            facts = self.cortex.recall(
                sender_qq, account_id, limit=self.cfg.facts_limit, mood=mood
            )
            if allowed_chat_ids is not None:
                facts = [
                    f for f in facts
                    if (getattr(f, "source_chat", "") or "") in allowed_chat_ids
                    or (allow_unknown_facts and not getattr(f, "source_chat", "")
                        and getattr(f, "subject_id", "") == sender_qq)
                ]
            ctx.facts = facts
            if facts:
                line = "· 记得：" + "；".join(f.text[:30] for f in facts[:5])
                ctx.parts.append(("fact", line))

        # 三级半：变化史
        if "history" in routes and allowed_chat_ids is None:
            history = self._history_line(sender_qq, account_id)
            if history:
                ctx.parts.append(("history", history))

        # 四级：联想扩展（前面都很空才带）
        used = self._used_chars(ctx.parts)
        if used < max_chars * 0.3 and self.graph and allow_related:
            related = self._related_line(sender_qq, account_id)
            if related:
                ctx.parts.append(("related", related))

        # 裁剪
        ctx.text = self._trim(ctx.parts, max_chars)
        ctx.chars = len(ctx.text)
        return ctx

    # ==================================================================
    # 各级拼装
    # ==================================================================

    def _profile_line(self, qq: str, account_id: str, *, detail: bool = True) -> str:
        """一级：人名 + 关键特征，一行。有画像就用画像。"""
        if self.profiles is not None:
            try:
                budget = 200
                if self.settings is not None:
                    budget = int(self.settings.get("memory.profile_chars") or 200)
                rich = self.profiles.format_for_prompt(qq, account_id, budget=budget)
                if rich:
                    return rich
            except Exception:  # noqa: BLE001
                pass
        row = self.db.query_one(
            "SELECT name, alias, style, persona, handle FROM people "
            "WHERE qq = ? AND (account_id = ? OR ? = '') LIMIT 1",
            [qq, account_id, account_id],
        )
        if row is None:
            return ""

        name = row["alias"] or row["name"] or qq
        bits = [f"{name}({qq})"]
        if detail:
            for key in ("style", "persona", "handle"):
                if row[key]:
                    bits.append(row[key])
        return "· " + "，".join(bits)

    def _recent_line(
        self,
        qq: str,
        account_id: str,
        days: int = 7,
        limit: int = 5,
        exclude_id: int | None = None,
        chat_id: str = "",
    ) -> str:
        """二级：最近说了什么（只取本账号，排掉当前这条）。"""
        if chat_id:
            eps = self.hippo.recent(chat_id, limit=limit + 1, account_id=account_id)
            # 当前会话没有任何这个人的话：不要把别人的最近发言当成他的画像
            if not any((e.sender_qq == qq) for e in eps):
                eps = []
        else:
            eps = self.hippo.by_person(qq, days=days, limit=limit + 1, account_id=account_id)
        texts = [e.text[:40] for e in eps if e.text and e.id != exclude_id][:limit]
        if not texts:
            return ""
        return "· 最近：" + "；".join(texts)

    def _history_line(self, qq: str, account_id: str) -> str:
        """三级半：以前和现在的差别。"""
        rows = self.db.query(
            """
            SELECT predicate, COUNT(*) AS n FROM facts
            WHERE subject_id = ? AND gone = 0 AND (account_id = ? OR ? = '')
              AND valid_until IS NOT NULL
            GROUP BY predicate HAVING n > 0 ORDER BY n DESC LIMIT 3
            """,
            [qq, account_id, account_id],
        )
        if not rows:
            return ""

        lines = []
        for r in rows[:2]:
            hist = self.cortex.history_of(qq, r["predicate"], account_id)
            if len(hist) < 2:
                continue
            pieces = []
            for f in hist:
                when = "以前" if f.valid_until else "现在"
                pieces.append(f"{when}{f.text}")
            lines.append("；".join(pieces))

        if not lines:
            return ""
        return "· 变化：" + " | ".join(lines)

    def _related_line(self, qq: str, account_id: str, limit: int = 5) -> str:
        """四级：顺着图想到的人和事。"""
        if not self.graph:
            return ""
        node = self.graph.get_node(account_id, "person", qq)
        if node is None:
            return ""

        results = self.graph.walk(node.id, depth=2, limit=limit + 1)
        labels = []
        for r in results:
            if r.via in ("said", "mentioned", "replied_to"):
                continue   # 消息节点和关系节点不往外说
            n = self.db.query_one("SELECT label, kind FROM nodes WHERE id = ?", [r.node_id])
            if n and n["label"] and n["kind"] == "person":
                labels.append(n["label"])
        if not labels:
            return ""
        return "· 相关：" + "、".join(labels[:limit])

    # ==================================================================
    # 裁剪
    # ==================================================================

    def _used_chars(self, parts: list[tuple[str, str]]) -> int:
        return sum(len(p[1]) for p in parts)

    def _trim(self, parts: list[tuple[str, str]], max_chars: int) -> str:
        """按字数裁剪。顺序就是优先级，先砍最后的。"""
        out: list[str] = []
        total = 0
        for _, content in parts:
            if total + len(content) > max_chars:
                remain = max_chars - total
                if remain > 20:
                    out.append(content[:remain] + "…")
                break
            out.append(content)
            total += len(content)
        return "\n".join(out)

    # ==================================================================
    # 通用检索
    # ==================================================================

    def search(
        self,
        query: str,
        *,
        account_id: str = "",
        chat_id: str | None = None,
        sender_qq: str | None = None,
        limit: int = 20,
    ) -> list[ScoredItem]:
        """通用检索，返回带分的条目。"""
        items: list[ScoredItem] = []
        now = time.time()

        # 原话
        eps = self.hippo.search(
            chat_id=chat_id, sender_qq=sender_qq, keyword=query, limit=limit
        )
        for e in eps:
            age_days = max((now - e.event_at) / 86400, 0)
            items.append(
                ScoredItem(
                    kind="episode", ref_id=str(e.id), text=e.text,
                    score=0.5 * (0.5 ** (age_days / 7)) + 0.2,
                    tier=2, event_at=e.event_at,
                )
            )

        # 事实
        if sender_qq:
            for f in self.cortex.facts_of(sender_qq, account_id, limit=limit):
                s = self.cortex.score(f, now)
                items.append(
                    ScoredItem(
                        kind="fact", ref_id=str(f.id), text=f.text,
                        score=s, tier=f.tier,
                        event_at=f.valid_from or f.recorded_at, hits=f.hits,
                    )
                )

        items.sort(key=lambda x: -x.score)
        return items[:limit]

    # ==================================================================
    # 调试
    # ==================================================================

    def explain(
        self, *, account_id: str, chat_id: str, sender_qq: str, text: str = ""
    ) -> Explanation:
        """为什么取了这些。"""
        routes = classify_intent(text)
        ex = Explanation(routes=routes)
        ex.profile = self._profile_line(sender_qq, account_id)

        eps = self.hippo.by_person(sender_qq, days=self.cfg.recent_days, limit=5)
        ex.recent = [e.text[:40] for e in eps]

        facts = self.cortex.facts_of(sender_qq, account_id, limit=20)
        now = time.time()
        for f in facts[:8]:
            ex.facts.append((f.text[:30], self.cortex.score(f, now)))

        if self.graph:
            node = self.graph.get_node(account_id, "person", sender_qq)
            if node:
                for r in self.graph.walk(node.id, depth=2, limit=5):
                    n = self.db.query_one("SELECT label FROM nodes WHERE id = ?", [r.node_id])
                    if n and n["label"]:
                        ex.related.append(f"{n['label']}({r.weight:.2f})")

        ctx = self.build_context(
            account_id=account_id, chat_id=chat_id, sender_qq=sender_qq, text=text
        )
        ex.total_chars = ctx.chars
        return ex
