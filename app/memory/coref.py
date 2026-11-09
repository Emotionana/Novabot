"""指代消歧：属于前额叶（检索调度）的一部分。

三件事：
  1. 名字 → QQ 号（别名表）
  2. 代词 → 谁（从上下文解析）
  3. 引用 → 展开原消息

核心原则：宁可问，不要猜。
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

from ..db import Database
from ..models import Episode

log = logging.getLogger(__name__)


# 别名来源的可信度
SOURCE_RANK = {
    "remark": 5,
    "card": 4,
    "nickname": 3,
    "self": 2,
    "inferred": 1,
}

# 代词
PRONOUNS = (
    "他", "她", "它", "TA", "ta",
    "那人", "这个人", "那个人", "那位", "这位",
    "楼上", "楼上那位", "刚才那个", "前面那个",
)


@dataclass
class ResolveResult:
    """一次解析的结果。"""

    qq: str | None = None
    name: str = ""
    method: str = ""        # alias / pronoun / quote / reply / none
    confidence: float = 0.0
    ambiguous: bool = False
    candidates: list[str] = None

    def __post_init__(self):
        if self.candidates is None:
            self.candidates = []

    @property
    def ok(self) -> bool:
        return self.qq is not None


class AliasStore:
    """别名表：名字 → QQ 号。"""

    def __init__(self, db: Database):
        self.db = db
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS person_aliases (
              alias      TEXT NOT NULL,
              qq         TEXT NOT NULL,
              account_id TEXT NOT NULL,
              source     TEXT NOT NULL DEFAULT 'nickname',
              chat_id    TEXT,
              weight     REAL NOT NULL DEFAULT 1.0,
              first_seen REAL NOT NULL,
              last_seen  REAL NOT NULL,
              PRIMARY KEY (alias, qq, account_id, chat_id)
            )
            """
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_alias_lookup ON person_aliases(alias, account_id)"
        )

    def add(
        self, alias: str, qq: str, account_id: str, *,
        source: str = "nickname", chat_id: str | None = None,
    ) -> None:
        if not alias or not qq:
            return
        # 不用 NULL：SQLite 里 NULL 互不相等，主键含 NULL 时
        # ON CONFLICT 永远不命中，昵称别名会无限重复插入
        chat_id = chat_id or ""
        now = time.time()
        self.db.execute(
            """
            INSERT INTO person_aliases (alias, qq, account_id, source, chat_id, weight, first_seen, last_seen)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(alias, qq, account_id, chat_id) DO UPDATE SET
              last_seen = excluded.last_seen,
              source = CASE WHEN excluded.source IN ('remark','card') THEN excluded.source ELSE person_aliases.source END
            """,
            [alias, qq, account_id, source, chat_id,
             1.0 if source in ("remark", "card") else 0.7, now, now],
        )

    def resolve(self, name: str, account_id: str, chat_id: str | None = None) -> ResolveResult:
        """名字 → QQ 号。撞名返回 ambiguous。"""
        if not name:
            return ResolveResult(method="none")

        rows = self.db.query(
            """
            SELECT qq, source, chat_id, weight FROM person_aliases
            WHERE alias = ? AND account_id = ?
              AND (chat_id = '' OR chat_id = ?)
            """,
            [name, account_id, chat_id],
        )
        if not rows:
            return ResolveResult(name=name, method="none")

        # 按可信度排
        rows = sorted(
            rows,
            key=lambda r: (-SOURCE_RANK.get(r["source"], 0), -r["weight"]),
        )

        # 唯一候选
        qqs = list({r["qq"] for r in rows})
        if len(qqs) == 1:
            return ResolveResult(
                qq=qqs[0], name=name, method="alias",
                confidence=0.9 if rows[0]["source"] in ("remark", "card") else 0.75,
            )

        # 多个候选：有本群名片的赢
        for r in rows:
            if r["source"] == "card" and r["chat_id"] == chat_id:
                return ResolveResult(
                    qq=r["qq"], name=name, method="alias_card",
                    confidence=0.8, candidates=qqs,
                )

        # 分不出
        log.info("名字「%s」撞车：%s", name, qqs)
        return ResolveResult(name=name, method="ambiguous", ambiguous=True, candidates=qqs)


class Resolver:
    """代词和引用解析。"""

    def __init__(self, db: Database, aliases: AliasStore):
        self.db = db
        self.aliases = aliases
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS pronoun_resolutions (
              id          INTEGER PRIMARY KEY,
              chat_id     TEXT NOT NULL,
              episode_id  INTEGER,
              pronoun     TEXT,
              resolved_to TEXT,
              method      TEXT,
              confidence  REAL,
              created_at  REAL NOT NULL
            )
            """
        )
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS name_ambiguities (
              id          INTEGER PRIMARY KEY,
              alias       TEXT NOT NULL,
              chat_id     TEXT,
              candidates  TEXT,
              resolved_to TEXT,
              created_at  REAL NOT NULL
            )
            """
        )

    # -- 入口 ------------------------------------------------------------

    def resolve_text(
        self,
        text: str,
        *,
        account_id: str,
        chat_id: str,
        recent: list[Episode],
        speaker: str = "",
        bot_qq: str = "",
    ) -> ResolveResult:
        """从一段话里解析出「他在说谁」。"""
        if not text:
            return ResolveResult(method="none")

        # 1. 先看有没有直接的已知名字
        for token in _candidate_names(text):
            r = self.aliases.resolve(token, account_id, chat_id)
            if r.ok:
                return r
            if r.ambiguous:
                return r

        # 2. 再看代词
        pronoun = _find_pronoun(text)
        if pronoun:
            qq = self.resolve_pronoun(pronoun, chat_id, speaker, recent, bot_qq)
            return ResolveResult(
                qq=qq, name=pronoun, method="pronoun",
                confidence=0.6 if qq else 0.0,
            )

        return ResolveResult(method="none")

    def resolve_pronoun(
        self,
        pronoun: str,
        chat_id: str,
        speaker: str,
        recent: list[Episode],
        bot_qq: str = "",
    ) -> str | None:
        """代词 → QQ 号。解析不出返回 None。"""
        if not recent:
            return None

        # 「你」→ 说我自己
        if pronoun in ("你", "您"):
            return bot_qq

        # 上一条是我说的 → 「他」指刚才说话的那个人
        if recent and recent[-1].sender_qq == bot_qq:
            for e in reversed(recent[:-1]):
                if e.sender_qq != bot_qq and e.sender_qq != speaker:
                    return e.sender_qq

        # 最近几条里，除了说话人之外最后出现的那个
        for e in reversed(recent[-6:]):
            if e.sender_qq and e.sender_qq not in (speaker, bot_qq):
                return e.sender_qq

        # 「楼上」→ 直接上一条
        if pronoun in ("楼上", "楼上那位"):
            return recent[-1].sender_qq if recent else None

        return None

    # -- 引用展开 --------------------------------------------------------

    def expand_quote(self, reply_to: int | None) -> str:
        """被引用的原消息，拼成一行。"""
        if not reply_to:
            return ""
        row = self.db.query_one(
            "SELECT sender_nick, sender_card, text FROM episodes WHERE id = ?",
            [reply_to],
        )
        if row is None:
            return ""
        name = row["sender_card"] or row["sender_nick"] or "某人"
        text = (row["text"] or "")[:60]
        return f"[引用 {name}：{text}]"

    def format_with_quote(self, msg) -> str:
        """把一条消息连同它的引用一起格式化。"""
        name = getattr(msg, "sender_card", None) or getattr(msg, "sender_nick", None) or "某人"
        body = getattr(msg, "text", "") or ""
        quote = self.expand_quote(getattr(msg, "reply_to", None))
        if quote:
            return f"{quote}\n{name}：{body}"
        return f"{name}：{body}"

    # -- 话题 ------------------------------------------------------------

    def current_topic(self, recent: list[Episode], window: int = 5, min_len: int = 10) -> str:
        """最近一个「有内容」的话题。"""
        for e in reversed(recent[-window:]):
            t = (e.text or "").strip()
            if len(t) >= min_len:
                return t[:50]
        return ""

    def detect_topic_break(self, recent: list[Episode], threshold: float = 0.25) -> bool:
        """有没有话题断裂（相邻两条几乎没共同词）。"""
        if len(recent) < 3:
            return False
        a, b = recent[-2].text or "", recent[-1].text or ""
        if not a or not b:
            return False
        return _jaccard(a, b) < threshold

    # -- 群聊上下文 ------------------------------------------------------

    def format_group_context(
        self, eps: list[Episode], *, use_card: bool = True, max_len: int = 60,
    ) -> str:
        """群聊上下文：每条都带说话人，用群里的称呼。"""
        lines: list[str] = []
        for e in eps:
            name = (e.sender_card if use_card else None) or e.sender_nick or e.sender_qq
            text = (e.text or "")[:max_len]
            if not text:
                continue
            quote = self.expand_quote(e.reply_to)
            if quote:
                lines.append(quote)
            lines.append(f"{name}：{text}")
        return "\n".join(lines)

    # -- 记录 ------------------------------------------------------------

    def log_pronoun(
        self, chat_id: str, ep_id: int | None, pronoun: str,
        resolved_to: str | None, method: str, confidence: float,
    ) -> None:
        self.db.execute(
            "INSERT INTO pronoun_resolutions "
            "(chat_id, episode_id, pronoun, resolved_to, method, confidence, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            [chat_id, ep_id, pronoun, resolved_to, method, confidence, time.time()],
        )

    def log_ambiguity(self, alias: str, chat_id: str, candidates: list[str]) -> None:
        self.db.execute(
            "INSERT INTO name_ambiguities (alias, chat_id, candidates, resolved_to, created_at) "
            "VALUES (?,?,?,?,?)",
            [alias, chat_id, json.dumps(candidates, ensure_ascii=False), None, time.time()],
        )


# ---------------------------------------------------------------------------
# 文本工具
# ---------------------------------------------------------------------------

_RE_WORD = re.compile(r"[\u4e00-\u9fff]{2,6}|[a-zA-Z][a-zA-Z0-9_]{1,20}")


def _candidate_names(text: str) -> list[str]:
    """从文本里抠出可能是名字的词。"""
    return _RE_WORD.findall(text)


def _find_pronoun(text: str) -> str:
    """找出文本里的代词。长的优先。"""
    for p in sorted(PRONOUNS, key=len, reverse=True):
        if p in text:
            return p
    if "你" in text:
        return "你"
    return ""


def _jaccard(a: str, b: str) -> float:
    """两个短句的字符重叠度。"""
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
