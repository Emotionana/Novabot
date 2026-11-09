"""用户画像：属于新皮层（语义记忆）的一部分。

存的是「这个人是什么样」，不是「发生过什么」。
数字比形容词准：说话平均 8 字，比「说话简短」有用。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from ..db import Database

log = logging.getLogger(__name__)


@dataclass
class SpeechPattern:
    """说话习惯，纯统计出来，不调模型。"""

    avg_len: float = 0.0
    max_len: int = 0
    q_ratio: float = 0.0        # 问句比例
    emoji_ratio: float = 0.0    # 带表情比例
    punctuation: str = "normal"  # sparse / normal / heavy
    sample_count: int = 0

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str | None) -> SpeechPattern:
        if not s:
            return cls()
        try:
            return cls(**json.loads(s))
        except (json.JSONDecodeError, TypeError):
            return cls()


@dataclass
class Interest:
    """一个兴趣点。"""

    topic: str = ""
    weight: float = 0.5
    last_mention: float = 0.0
    evidence: list[int] = field(default_factory=list)


@dataclass
class Profile:
    """一个人的完整画像。"""

    qq: str = ""
    account_id: str = ""
    name: str = ""
    alias: str = ""
    age_group: str = ""       # 学生 / 上班 / 未知
    occupation: str = ""
    location: str = ""
    sleep_pattern: str = ""   # early_bird / night_owl / irregular
    family: list[str] = field(default_factory=list)
    speech: SpeechPattern = field(default_factory=SpeechPattern)
    mood_baseline: float = 0.0
    sensitive_topics: list[str] = field(default_factory=list)
    like: list[Interest] = field(default_factory=list)
    dislike: list[Interest] = field(default_factory=list)
    frequent: list[str] = field(default_factory=list)
    relation_tag: str = ""
    trust_level: int = 0       # 0 陌生 / 1 认识 / 2 熟 / 3 很熟
    last_summary: str = ""

    @property
    def display(self) -> str:
        return self.alias or self.name or self.qq


class ProfileStore:
    """画像的读写。

    画像数据 90% 存在 people 表里，兴趣单独一张表便于衰减。
    """

    def __init__(self, db: Database):
        self.db = db
        self._ensure_schema()
        self._cache: dict[str, Profile] = {}

    def _ensure_schema(self) -> None:
        # 画像字段已收编进 schema v9，不再运行时 ALTER
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS person_interests (
              qq          TEXT NOT NULL,
              account_id  TEXT NOT NULL,
              topic       TEXT NOT NULL,
              kind        TEXT NOT NULL DEFAULT 'like',   -- like / dislike
              weight      REAL NOT NULL DEFAULT 0.5,
              last_mention REAL,
              evidence    TEXT,
              created_at  REAL NOT NULL,
              PRIMARY KEY (qq, account_id, topic, kind)
            )
            """
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_pi_qq ON person_interests(qq, account_id, kind, weight DESC)"
        )

        # 画像变更历史
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS profile_history (
              id         INTEGER PRIMARY KEY,
              qq         TEXT NOT NULL,
              account_id TEXT NOT NULL,
              field      TEXT NOT NULL,
              old_value  TEXT,
              new_value  TEXT,
              changed_at REAL NOT NULL
            )
            """
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ph_qq ON profile_history(qq, changed_at DESC)"
        )

    # -- 读 --------------------------------------------------------------

    def get(self, qq: str, account_id: str = "") -> Profile | None:
        key = f"{account_id}:{qq}"
        if key in self._cache:
            return self._cache[key]

        row = self.db.query_one(
            "SELECT * FROM people WHERE qq = ? AND account_id = ?",
            [qq, account_id or self._guess_account(qq)],
        )
        if row is None:
            return None

        p = Profile(
            qq=row["qq"],
            account_id=row["account_id"],
            name=row["name"] or "",
            alias=row["alias"] or "",
            age_group=row["age_group"] or "",
            occupation=row["occupation"] or "",
            location=row["location"] or "",
            sleep_pattern=row["sleep_pattern"] or "",
            family=json.loads(row["family"] or "[]"),
            speech=SpeechPattern.from_json(row["speech_pattern"]),
            mood_baseline=row["mood_baseline"] or 0.0,
            sensitive_topics=json.loads(row["sensitive_topics"] or "[]"),
            relation_tag=row["relation_tag"] or "",
            trust_level=row["trust_level"] or 0,
            last_summary=row["last_summary"] or "",
        )

        # 兴趣
        for r in self.db.query(
            "SELECT * FROM person_interests WHERE qq = ? AND account_id = ? ORDER BY weight DESC",
            [p.qq, p.account_id],
        ):
            item = Interest(
                topic=r["topic"],
                weight=r["weight"],
                last_mention=r["last_mention"] or 0,
                evidence=json.loads(r["evidence"] or "[]"),
            )
            (p.like if r["kind"] == "like" else p.dislike).append(item)

        self._cache[key] = p
        return p

    def _guess_account(self, qq: str) -> str:
        row = self.db.query_one("SELECT account_id FROM people WHERE qq = ? LIMIT 1", [qq])
        return row["account_id"] if row else ""

    # -- 写 --------------------------------------------------------------

    def set_field(self, qq: str, account_id: str, field: str, value) -> None:
        """改一个字段，记历史。"""
        if field not in (
            "age_group", "occupation", "location", "sleep_pattern",
            "sensitive_topics", "trust_level", "last_summary", "relation_tag",
        ):
            raise ValueError(f"不支持改的字段：{field}")

        old = self.db.query_one(
            f"SELECT {field} AS v FROM people WHERE qq = ? AND account_id = ?",
            [qq, account_id],
        )
        old_v = old["v"] if old else None

        new_v = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
        if isinstance(old_v, str) and old_v == new_v:
            return

        self.db.execute(
            f"UPDATE people SET {field} = ?, updated_at = ? WHERE qq = ? AND account_id = ?",
            [new_v, time.time(), qq, account_id],
        )
        if old_v is not None:
            self.db.execute(
                "INSERT INTO profile_history (qq, account_id, field, old_value, new_value, changed_at) "
                "VALUES (?,?,?,?,?,?)",
                [qq, account_id, field, str(old_v), str(new_v), time.time()],
            )
        self._cache.pop(f"{account_id}:{qq}", None)

    def add_interest(
        self, qq: str, account_id: str, topic: str, *, kind: str = "like",
        weight: float = 0.5, evidence: int | None = None,
    ) -> None:
        now = time.time()
        row = self.db.query_one(
            "SELECT weight, evidence FROM person_interests "
            "WHERE qq=? AND account_id=? AND topic=? AND kind=?",
            [qq, account_id, topic, kind],
        )
        if row:
            ev = json.loads(row["evidence"] or "[]")
            if evidence and evidence not in ev:
                ev.append(evidence)
            self.db.execute(
                "UPDATE person_interests SET weight = ?, last_mention = ?, evidence = ? "
                "WHERE qq=? AND account_id=? AND topic=? AND kind=?",
                [min(1.0, row["weight"] + 0.1), now, json.dumps(ev), qq, account_id, topic, kind],
            )
        else:
            self.db.execute(
                "INSERT INTO person_interests (qq, account_id, topic, kind, weight, last_mention, evidence, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [qq, account_id, topic, kind, weight, now,
                 json.dumps([evidence] if evidence else []), now],
            )
        self._cache.pop(f"{account_id}:{qq}", None)

    # -- 统计 ------------------------------------------------------------

    def update_speech_pattern(self, qq: str, account_id: str, days: int = 30) -> SpeechPattern | None:
        """算说话习惯。零成本，纯计算。"""
        since = time.time() - days * 86400
        rows = self.db.query(
            "SELECT text, event_at FROM episodes "
            "WHERE sender_qq = ? AND account_id = ? AND gone = 0 AND event_at > ? "
            "AND text IS NOT NULL AND text != ''",
            [qq, account_id, since],
        )
        if len(rows) < 10:
            return None

        texts = [r["text"] for r in rows]
        n = len(texts)
        lens = [len(t) for t in texts]
        q = sum(1 for t in texts if "?" in t or "？" in t)
        emo = sum(1 for t in texts if "[" in t)
        punct = sum(1 for t in texts if any(c in t for c in "。！？，"))

        pattern = SpeechPattern(
            avg_len=round(sum(lens) / n, 1),
            max_len=max(lens),
            q_ratio=round(q / n, 3),
            emoji_ratio=round(emo / n, 3),
            punctuation="heavy" if punct / n > 0.7 else ("sparse" if punct / n < 0.3 else "normal"),
            sample_count=n,
        )
        self.db.execute(
            "UPDATE people SET speech_pattern = ?, updated_at = ? WHERE qq = ? AND account_id = ?",
            [pattern.to_json(), time.time(), qq, account_id],
        )
        self._cache.pop(f"{account_id}:{qq}", None)
        return pattern

    def decay_interests(self, qq: str, account_id: str, *, days: int = 60, floor: float = 0.3) -> int:
        """很久没提的兴趣降权，降到底就删。"""
        cutoff = time.time() - days * 86400
        n = self.db.execute(
            "UPDATE person_interests SET weight = weight * 0.8 "
            "WHERE qq = ? AND account_id = ? AND (last_mention IS NULL OR last_mention < ?)",
            [qq, account_id, cutoff],
        )
        self.db.execute(
            "DELETE FROM person_interests WHERE qq = ? AND account_id = ? AND weight < ?",
            [qq, account_id, floor],
        )
        self._cache.pop(f"{account_id}:{qq}", None)
        return n

    # -- 拼给模型看 ------------------------------------------------------

    def format_for_prompt(self, qq: str, account_id: str = "", budget: int = 200) -> str:
        """画像拼成几行给模型看。"""
        p = self.get(qq, account_id)
        if p is None:
            return ""

        parts: list[str] = []

        # 基本信息一行
        bits = [p.display]
        if p.age_group:
            bits.append(p.age_group)
        if p.occupation:
            bits.append(p.occupation)
        if p.location:
            bits.append(f"在{p.location}")
        if p.relation_tag:
            bits.append(f"（{p.relation_tag}）")
        parts.append("· " + "，".join(bits))

        # 说话习惯用数字
        if p.speech.avg_len:
            parts.append(f"· 说话平均 {p.speech.avg_len:.0f} 字一条")
        if p.speech.q_ratio > 0.3:
            parts.append("· 爱问问题")

        # 兴趣
        if p.like:
            likes = "、".join(i.topic for i in p.like[:5])
            parts.append(f"· 喜欢：{likes}")
        if p.dislike:
            dislikes = "、".join(i.topic for i in p.dislike[:3])
            parts.append(f"· 不喜欢：{dislikes}")

        # 敏感
        if p.sensitive_topics:
            parts.append(f"· 别提：{'、'.join(p.sensitive_topics)}")

        # 最近
        if p.last_summary:
            parts.append(f"· 最近：{p.last_summary}")

        return _trim(parts, budget)

    # -- 给决策用 --------------------------------------------------------

    def reply_budget(self, qq: str, account_id: str = "", base: int = 300) -> int:
        """按这个人的说话长度，调整回复的 token 上限。"""
        p = self.get(qq, account_id)
        if p is None or not p.speech.avg_len:
            return base
        avg = p.speech.avg_len
        if avg < 6:
            return int(base * 0.5)
        if avg > 40:
            return int(base * 1.5)
        return base

    def tone_hint(self, qq: str, account_id: str = "") -> str:
        """语气提示。"""
        p = self.get(qq, account_id)
        if p is None:
            return ""

        tips: list[str] = []
        if p.speech.emoji_ratio > 0.2:
            tips.append("这人爱发表情，可以多带点")
        if p.speech.q_ratio > 0.3:
            tips.append("爱问问题，多给点信息")
        if p.trust_level >= 2:
            tips.append("熟人了，说话可以随意")
        elif p.trust_level == 0:
            tips.append("不太熟，客气一点")
        if p.relation_tag in ("主人", "owner"):
            tips.append("是主人，可以亲近")
        return "。".join(tips)

    def is_sensitive(self, qq: str, text: str, account_id: str = "") -> str:
        """这段文字有没有碰到敏感话题，返回命中的话题。"""
        p = self.get(qq, account_id)
        if p is None:
            return ""
        for topic in p.sensitive_topics:
            if topic and topic in text:
                return topic
        return ""

    # -- 缓存 ------------------------------------------------------------

    def clear_cache(self) -> None:
        self._cache.clear()


def _trim(parts: list[str], budget: int) -> str:
    out: list[str] = []
    total = 0
    for p in parts:
        if total + len(p) > budget:
            remain = budget - total
            if remain > 20:
                out.append(p[:remain] + "…")
            break
        out.append(p)
        total += len(p)
    return "\n".join(out)
