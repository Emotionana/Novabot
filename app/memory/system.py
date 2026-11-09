"""记忆系统的总入口。

把六个模块聚在一起，对外只暴露三个方法：
  remember()       写入
  recall()         检索
  build_context()  拼上下文

六个模块都接上了：海马 / 皮层 / 杏仁核 / 图谱 / 画像 / 召回。
"""

from __future__ import annotations

import logging
import re
import time

from .. import config
from ..db import Database, get_db
from ..models import Episode, InboundMessage, ScoredItem
from ..settings import Settings
from .amygdala import Amygdala
from .cortex import Cortex
from .graph import Graph
from .hippocampus import Hippocampus
from .knowledge import KnowledgeStore
from .migrate import init_schema
from .profile import ProfileStore
from .recall import Recall

log = logging.getLogger(__name__)


class MemorySystem:
    def __init__(self, db: Database, cfg: config.MemoryConfig | None = None):
        self.db = db
        self.cfg = cfg or config.get().memory

        init_schema(db)

        self.hippocampus = Hippocampus(db, retention_days=self.cfg.episode_retention_days)
        self.settings = Settings(db)
        self.cortex = Cortex(db, self.settings)

        # 六个模块都接上：画像 / 召回 / 图谱 / 情绪；后三个按开关工作
        self.profiles = ProfileStore(db)
        self.knowledge = KnowledgeStore(db)
        self.amygdala = Amygdala(db, self.settings)
        self.graph = Graph(db, self.settings)
        self.selfmodel = None
        self.recaller = Recall(
            db,
            self.hippocampus,
            self.cortex,
            graph=self.graph,
            amygdala=self.amygdala,
            settings=self.settings,
            profiles=self.profiles,
        )

    # -- 写入 ------------------------------------------------------------

    def remember(self, msg: InboundMessage) -> int:
        """写一条消息进记忆，返回 episode id。

        写入后顺手做两件小事（都可关）：
        - 有情绪的，记一笔 emotion（只存置信度高的，避免刷表）
        - @ / 回复，建一条关系边（不是每条消息都建，防止图无限膨胀）
        """
        ep_id = self.hippocampus.write(msg)
        try:
            if msg.sender_qq == "self":
                return ep_id
            ep = None
            if self._on("memory.emotion_enabled", True):
                ep = self.hippocampus.get(ep_id)
                if ep is not None:
                    j = self.amygdala.rule_judge(ep.text or "")
                    if j.sure:
                        self.amygdala.judge_episode(ep)
            if self._on("memory.graph_enabled", True) and (msg.at_list or msg.reply_to):
                ep = ep or self.hippocampus.get(ep_id)
                if ep is not None:
                    self.graph.rule_link(ep)
        except Exception as e:  # noqa: BLE001
            log.debug("remember 附加处理失败：%s", e)
        return ep_id

    def _on(self, key: str, default: bool) -> bool:
        try:
            return bool(self.settings.get(key))
        except Exception:  # noqa: BLE001
            return default

    # -- 检索 ------------------------------------------------------------

    def recall(
        self,
        query: str = "",
        *,
        chat_id: str | None = None,
        sender_qq: str | None = None,
        limit: int = 20,
    ) -> list[ScoredItem]:
        """通用检索。第 1 阶段只有关键词 + 时间。"""
        eps: list[Episode] = []
        if query:
            eps = self.hippocampus.search(
                chat_id=chat_id, sender_qq=sender_qq, keyword=query, limit=limit
            )
        if not eps and sender_qq:
            eps = self.hippocampus.by_person(
                sender_qq, days=self.cfg.recent_days, limit=limit
            )
        if not eps and chat_id:
            eps = self.hippocampus.recent(chat_id, limit=limit)

        now = time.time()
        items: list[ScoredItem] = []
        for e in eps:
            age_days = max((now - e.event_at) / 86400, 0)
            time_score = 0.5 ** (age_days / 7)
            items.append(
                ScoredItem(
                    kind="episode",
                    ref_id=str(e.id),
                    text=e.text,
                    score=0.6 * time_score + 0.4 * 0.5,
                    tier=2,
                    event_at=e.event_at,
                )
            )
        items.sort(key=lambda x: -x.score)
        return items[:limit]

    def _people_context(self, account_id: str, chat_id: str, text: str, *, max_chars: int = 260, detail: bool = True) -> str:
        """只返回公开人物资料，不返回任何聊天原文。"""
        if not text:
            return ""
        try:
            rows = self.db.query(
                "SELECT qq, name, alias, style, persona, handle FROM people "
                "WHERE account_id = ? AND (COALESCE(name,'') <> '' OR COALESCE(alias,'') <> '') "
                "ORDER BY last_seen DESC LIMIT 300",
                [account_id or "default"],
            )
        except Exception:  # noqa: BLE001
            return ""
        low = text.casefold()
        asks_who = bool(re.search(r"谁|是谁|哪个人|什么来头|资料|认识|身份|查一下", text))
        picked = []
        for r in rows:
            names = [str(r["name"] or ""), str(r["alias"] or "")]
            names += [x.strip() for x in str(r["alias"] or "").split(",") if x.strip()]
            if any(n and n.casefold() in low for n in names):
                picked.append(r)
        if not picked and asks_who and str(chat_id).startswith("group:") and self.db is not None:
            try:
                qqs = [str(x["sender_qq"]) for x in self.db.query(
                    "SELECT sender_qq, MAX(event_at) AS t FROM episodes "
                    "WHERE account_id=? AND chat_id=? AND gone=0 AND sender_qq<>'' "
                    "GROUP BY sender_qq ORDER BY t DESC LIMIT 12",
                    [account_id or "default", chat_id],
                )]
                index = {str(r["qq"]): r for r in rows}
                picked = [index[q] for q in qqs if q in index]
            except Exception:  # noqa: BLE001
                picked = []
        if not picked:
            return ""
        bits = []
        for r in picked[:6]:
            name = str(r["alias"] or r["name"] or "").strip() or str(r["qq"])
            extra = "；".join(str(r[k] or "").strip() for k in ("style", "persona", "handle") if str(r[k] or "").strip()) if detail else ""
            bits.append(f"{name}" + (f"：{extra[:80]}" if extra else ""))
        line = "【人物资料，不是聊天原文】\n" + "\n".join(f"- {b}" for b in bits)
        return line[:max_chars]

    def build_context(
        self,
        *,
        chat_id: str,
        sender_qq: str,
        text: str = "",
        account_id: str = "",
        max_chars: int | None = None,
        exclude_id: int | None = None,
        allowed_chat_ids: set[str] | None = None,
        profile_detail: bool = True,
        allow_related: bool = True,
        allow_unknown_facts: bool = False,
        allow_documents: bool = True,
    ) -> str:
        """拼一段给模型的记忆上下文。"""
        max_chars = max_chars or self.cfg.recall_chars
        ctx_text = self.recaller.build_context(
            account_id=account_id,
            chat_id=chat_id,
            sender_qq=sender_qq,
            text=text,
            max_chars=max_chars,
            exclude_id=exclude_id,
            allowed_chat_ids=allowed_chat_ids,
            profile_detail=profile_detail,
            allow_related=allow_related,
            allow_unknown_facts=allow_unknown_facts,
        ).text

        people = self._people_context(account_id, chat_id, text, max_chars=260, detail=profile_detail)
        if people:
            ctx_text = ((ctx_text + "\n" if ctx_text else "") + people).strip()

        # 文档知识库：只在召回后还有预算时带，最多 2 片
        if text and ctx_text is not None:
            room = max_chars - len(ctx_text) - 8
            if room > 80:
                extra = self.knowledge.context_line(
                    account_id=account_id, query=text, max_chars=min(240, room)
                )
                if extra:
                    ctx_text = (ctx_text + "\n【文档】\n" + extra).strip()
        return ctx_text

    # -- 人物 ------------------------------------------------------------

    def get_person(self, qq: str, account_id: str | None = None) -> str:
        """取一个人物的简述（第 1 阶段只返回名字）。"""
        sql = "SELECT name, alias, style, persona, handle FROM people WHERE qq = ?"
        params: list = [qq]
        if account_id:
            sql += " AND account_id = ?"
            params.append(account_id)
        row = self.db.query_one(sql, params)
        if row is None:
            return ""
        name = row["alias"] or row["name"] or qq
        bits = [f"{name}({qq})"]
        for key in ("style", "persona", "handle"):
            if row[key]:
                bits.append(row[key])
        return "，".join(bits)

    # -- 维护 ------------------------------------------------------------

    def daily_maintenance(self) -> dict[str, int]:
        """每日维护：过期、基线。"""
        out = {"expired": self.hippocampus.expire()}
        self.db.optimize()
        return out

    def stats(self) -> dict[str, int]:
        from .migrate import table_counts

        return table_counts(self.db)


def _trim(parts: list[str], max_chars: int) -> str:
    out: list[str] = []
    total = 0
    for p in parts:
        if total + len(p) > max_chars:
            remain = max_chars - total
            if remain > 20:
                out.append(p[:remain] + "…")
            break
        out.append(p)
        total += len(p)
    return "\n".join(out)


_memory: MemorySystem | None = None


def get_memory() -> MemorySystem:
    global _memory
    if _memory is None:
        _memory = MemorySystem(get_db())
    return _memory


def reset_memory(m: MemorySystem | None = None) -> None:
    global _memory
    _memory = m
