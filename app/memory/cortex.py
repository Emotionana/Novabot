"""新皮层：语义记忆。

存的是「长期知识」：事实、摘要、人物。
不是每条消息都写，是蒸馏产出。

关键在冲突处理：新的和旧的对不上时，旧的不删，标终止时间。
"""

from __future__ import annotations

import json
import logging
import math
import time

from ..db import Database
from ..models import Episode, Fact

log = logging.getLogger(__name__)


class Cortex:
    def __init__(self, db: Database, settings=None):
        self.db = db
        self.settings = settings

    # ==================================================================
    # 事实
    # ==================================================================

    def write_fact(self, fact: Fact) -> tuple[int, str]:
        """写一条事实，处理冲突。返回 (id, 处理方式)。"""
        now = time.time()
        fact.recorded_at = fact.recorded_at or now
        fact.valid_from = fact.valid_from or now

        # 1. 找同类旧事实（同 subject + 同 predicate，且现在仍成立）
        olds = self.db.query(
            """
            SELECT * FROM facts
            WHERE account_id = ? AND subject_id = ? AND predicate IS ?
              AND gone = 0 AND (valid_until IS NULL OR valid_until > ?)
            ORDER BY recorded_at DESC
            """,
            [fact.account_id, fact.subject_id, fact.predicate, now],
        )

        # 2. 先把结论全部算出来，再动手。
        #    旧实现在遇到 duplicate 时提前 return，之前判定要终止的旧事实就回填不到了，
        #    留下"新旧并存"的半截状态。
        verdicts: list[tuple[Fact, str]] = []
        for row in olds:
            old = _row_to_fact(row)
            verdicts.append((old, self._compare(old, fact)))

        duplicates = [old for old, verdict in verdicts if verdict == "duplicate"]
        if duplicates:
            # 同一件事又说了一遍：不加行，只加命中
            for old in duplicates:
                self.db.execute(
                    "UPDATE facts SET hits = hits + 1, last_hit = ? WHERE id = ?",
                    [now, old.id],
                )
            return duplicates[0].id, "duplicate"

        to_supersede = [old.id for old, verdict in verdicts if verdict == "new_wins"]
        conflicts = [old for old, verdict in verdicts if verdict == "conflict"]

        if conflicts:
            result = "conflict"
        elif to_supersede:
            result = "superseded"
        else:
            result = "inserted"

        # 3. 写新事实（先写，因为外键要指向它）
        fact_id = self.db.insert(
            """
            INSERT INTO facts (
              account_id, subject_type, subject_id, predicate, object, text, tier,
              valid_from, valid_until, recorded_at, superseded_by, supersedes,
              hits, last_hit, source_chat, source_ep, confidence, gone
            ) VALUES (?,?,?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?,?)
            """,
            [
                fact.account_id, fact.subject_type, fact.subject_id,
                fact.predicate, fact.object, fact.text, fact.tier,
                fact.valid_from, fact.valid_until, fact.recorded_at,
                fact.superseded_by, to_supersede[0] if to_supersede else fact.supersedes,
                max(fact.hits, 1), fact.last_hit, fact.source_chat, fact.source_ep,
                fact.confidence, 0,
            ],
        )

        # 4. 回填旧事实。
        #    确定的更新（时间差超过一天）直接终止；
        #    冲突（同一时间段两种说法）要看证据：置信度够高且有出处才敢终止，
        #    否则只降权，两条都留着——猜错的代价比留着大。
        for old_id in to_supersede:
            self.db.execute(
                "UPDATE facts SET valid_until = ?, superseded_by = ? WHERE id = ?",
                [fact.valid_from, fact_id, old_id],
            )

        for old in conflicts:
            if fact.confidence >= 0.7 and fact.source_ep:
                log.info("事实冲突，新证据充分，旧「%s」标终止", old.text[:30])
                self.db.execute(
                    "UPDATE facts SET valid_until = ?, superseded_by = ? WHERE id = ?",
                    [fact.valid_from, fact_id, old.id],
                )
            else:
                log.info("事实冲突，证据不足，旧「%s」降权保留", old.text[:30])
                self.db.execute(
                    "UPDATE facts SET confidence = confidence * 0.6 WHERE id = ?", [old.id]
                )

        return fact_id, result

    def _compare(self, old: Fact, new: Fact) -> str:
        """新和旧的关系。

        顺序：先判重复，再判新旧。
        判新旧用 recorded_at，不用 valid_from——
        补录旧聊天时 valid_from 会差很远，用它会把复述判成冲突。
        """
        # 1. 三元组一致 → 同一条，只算复述
        if (
            old.subject_id == new.subject_id
            and old.predicate == new.predicate
            and old.object == new.object
        ):
            return "duplicate"

        # 2. 文本高度相似 → 也是重复
        if _similarity(old.text, new.text) > 0.9:
            return "duplicate"

        # 3. 记录时间差超过一天 → 后来的赢（这是更新）
        if abs((new.recorded_at or time.time()) - (old.recorded_at or 0)) > 86400:
            return "new_wins"

        # 4. 剩下算冲突
        return "conflict"

    def get_fact(self, fact_id: int) -> Fact | None:
        row = self.db.query_one("SELECT * FROM facts WHERE id = ?", [fact_id])
        return _row_to_fact(row) if row else None

    def facts_of(
        self,
        subject_id: str,
        account_id: str = "",
        *,
        live_only: bool = True,
        limit: int = 50,
        min_confidence: float = 0.0,
    ) -> list[Fact]:
        """某人的事实。

        必须带 account_id：people 的主键是 (qq, account_id)，
        不带的话两个号都认识同一个 QQ 号时会串。
        """
        where = ["subject_id = ?", "gone = 0", "confidence >= ?"]
        params: list = [subject_id, min_confidence]
        if account_id:
            where.append("account_id = ?")
            params.append(account_id)
        if live_only:
            where.append("(valid_until IS NULL OR valid_until > ?)")
            params.append(time.time())

        rows = self.db.query(
            f"SELECT * FROM facts WHERE {' AND '.join(where)} "
            f"ORDER BY tier DESC, hits DESC, recorded_at DESC LIMIT ?",
            [*params, limit],
        )
        return [_row_to_fact(r) for r in rows]

    def facts_at(
        self, subject_id: str, when: float, account_id: str = "", limit: int = 20
    ) -> list[Fact]:
        """某个时间点，关于这个人的事成立的有哪些。"""
        rows = self.db.query(
            """
            SELECT * FROM facts
            WHERE subject_id = ? AND gone = 0
              AND (account_id = ? OR ? = '')
              AND (valid_from IS NULL OR valid_from <= ?)
              AND (valid_until IS NULL OR valid_until > ?)
            ORDER BY tier DESC LIMIT ?
            """,
            [subject_id, account_id, account_id, when, when, limit],
        )
        return [_row_to_fact(r) for r in rows]

    def history_of(self, subject_id: str, predicate: str, account_id: str = "") -> list[Fact]:
        """某方面的事实变化史，按时间排。"""
        rows = self.db.query(
            """
            SELECT * FROM facts
            WHERE subject_id = ? AND predicate = ? AND gone = 0
              AND (account_id = ? OR ? = '')
            ORDER BY COALESCE(valid_from, recorded_at)
            """,
            [subject_id, predicate, account_id, account_id],
        )
        return [_row_to_fact(r) for r in rows]

    def touch(self, fact_id: int) -> None:
        """标记这条被用了一次。"""
        self.db.execute(
            "UPDATE facts SET hits = hits + 1, last_hit = ? WHERE id = ?",
            [time.time(), fact_id],
        )

    def supersede(self, old_id: int, new_id: int, when: float | None = None) -> None:
        """手动标旧事实被新的替代。"""
        self.db.execute(
            "UPDATE facts SET valid_until = ?, superseded_by = ? WHERE id = ?",
            [when or time.time(), new_id, old_id],
        )

    def expire_facts(self, days: int = 180) -> int:
        """低 tier 的老事实标记过期。tier 0 和 3 不动。"""
        cutoff = time.time() - days * 86400
        return self.db.execute(
            """
            UPDATE facts SET gone = 1
            WHERE gone = 0 AND tier <= 2
              AND COALESCE(last_hit, recorded_at, valid_from, 0) < ?
            """,
            [cutoff],
        )

    # ==================================================================
    # 摘要
    # ==================================================================

    def write_gist(
        self,
        account_id: str,
        level: int,
        period_from: float,
        period_to: float,
        text: str,
        *,
        chat_id: str | None = None,
        sender_qq: str | None = None,
        source_eps: list[int] | None = None,
    ) -> int:
        return self.db.insert(
            """
            INSERT INTO gists
              (account_id, level, chat_id, sender_qq, period_from, period_to, text, source_eps, created_at, gone)
            VALUES (?,?,?,?,?,?,?,?,?,0)
            """,
            [
                account_id, level, chat_id, sender_qq, period_from, period_to,
                text, json.dumps(source_eps or []), time.time(),
            ],
        )

    def gists_of(
        self,
        *,
        chat_id: str | None = None,
        sender_qq: str | None = None,
        account_id: str = "",
        level: int | None = None,
        limit: int = 20,
    ) -> list[dict]:
        where = ["gone = 0"]
        params: list = []
        if chat_id:
            where.append("chat_id = ?")
            params.append(chat_id)
        if sender_qq:
            where.append("sender_qq = ?")
            params.append(sender_qq)
        if account_id:
            where.append("account_id = ?")
            params.append(account_id)
        if level is not None:
            where.append("level = ?")
            params.append(level)

        rows = self.db.query(
            f"SELECT * FROM gists WHERE {' AND '.join(where)} "
            f"ORDER BY period_to DESC LIMIT ?",
            [*params, limit],
        )
        return [dict(r) for r in rows]

    # ==================================================================
    # 检索打分
    # ==================================================================

    @staticmethod
    def _plasticity(settings) -> tuple[float, float]:
        if not settings:
            return 0.5, 5.0
        return (
            float(settings.get("memory.plasticity_per_hit") or 0),
            float(settings.get("memory.plasticity_max") or 5.0),
        )

    def _settings_for(self, account_id: str = ""):
        """记忆参数按账号取；没账号/没设置就回全局。"""
        if not self.settings or not account_id:
            return self.settings
        try:
            from ..settings import Settings

            return Settings(self.db, account_id=account_id)
        except Exception:  # noqa: BLE001
            return self.settings

    def _half_life_days(self, tier: int, settings=None) -> float | None:
        """tier0 永久；1/2/3 从参数读，读不到用旧默认。"""
        if tier <= 0:
            return None
        settings = settings or self.settings
        default = {3: 90, 2: 30, 1: 7}.get(tier, 30)
        try:
            if not settings:
                return float(default)
            if tier == 1:
                value = settings.get("memory.tier1_half_life_days")
            elif tier == 2:
                value = settings.get("memory.tier2_half_life_days")
            else:
                value = settings.get("memory.tier3_half_life_days")
            return max(1.0, float(value))
        except Exception:  # noqa: BLE001
            return float(default)

    def score(self, fact: Fact, now: float, *, mood: str | None = None, valence: float | None = None, arousal: float | None = None) -> float:
        """事实的检索分。"""
        # tier 0 置顶直出，不参与加权
        # 原因：0.5 ** (age / 1e9) 恒等于 1，等于给永久事实永远满分
        if fact.tier == 0:
            return 1e9

        age_days = max((now - (fact.valid_from or fact.recorded_at)) / 86400, 0)
        cfg = self._settings_for(getattr(fact, "account_id", ""))
        half_life = self._half_life_days(fact.tier, cfg)
        if half_life is None:
            time_score = 1.0
        else:
            boost = 1.0
            try:
                per, cap = self._plasticity(cfg)
                boost = min(1.0 + math.log1p(max(fact.hits or 0, 0)) * per, max(1.0, cap))
            except Exception:  # noqa: BLE001
                boost = 1.0
            time_score = 0.5 ** (age_days / (half_life * boost))

        # tier 1-3 → 0.6 / 0.8 / 1.0
        tier_score = (fact.tier + 2) / 5.0

        # 命中次数，固定分母，不查全表 max
        hit_score = math.log1p(fact.hits) / math.log1p(100)

        # 情绪加成拆两项
        emo_consistent = 0.0
        emo_arousal = 0.0
        if valence is not None:
            if mood == "bad" and valence < -0.3 or mood == "good" and valence > 0.3:
                emo_consistent = 1.0
            if emo_consistent:
                emo_arousal = (arousal or 0) * 0.5

        emo_score = 0.7 * emo_consistent + 0.3 * emo_arousal

        return (
            0.35 * time_score
            + 0.25 * tier_score
            + 0.10 * hit_score
            + 0.10 * emo_score
            + 0.20 * 0  # 语义相似度，没算向量时为 0
        )

    def recall(
        self,
        subject_id: str,
        account_id: str = "",
        *,
        limit: int = 6,
        mood: str | None = None,
        min_confidence: float = 0.6,
    ) -> list[Fact]:
        """取这个人最该带的几条事实。"""
        facts = self.facts_of(
            subject_id, account_id, live_only=True, limit=limit * 3,
            min_confidence=min_confidence,
        )
        now = time.time()
        scored = [(self.score(f, now, mood=mood), f) for f in facts]
        scored.sort(key=lambda x: -x[0])
        out = [f for _, f in scored[:limit]]
        for f in out:
            self.touch(f.id)
        return out

    # ==================================================================
    # 蒸馏
    # ==================================================================

    def apply_distill(
        self,
        account_id: str,
        chat_id: str,
        episodes: list[Episode],
        result: dict,
    ) -> dict[str, int]:
        """把模型蒸馏出来的东西落库。

        result 结构：
        {
          "facts": [{"subject","predicate","object","text","tier","source_idx"}],
          "relations": [{"subj","pred","obj"}],
          "gist": "...",
          "profile_updates": {...}
        }
        """
        stats = {"facts": 0, "duplicate": 0, "superseded": 0, "conflict": 0, "gists": 0}

        for f in result.get("facts", []):
            try:
                fact = Fact(
                    account_id=account_id,
                    subject_type=f.get("subject_type", "person"),
                    subject_id=str(f.get("subject") or ""),
                    predicate=f.get("predicate"),
                    object=f.get("object"),
                    text=f.get("text") or "",
                    tier=int(f.get("tier", 2)),
                    valid_from=_support_time(f, episodes),
                    recorded_at=time.time(),
                    source_chat=chat_id,
                    source_ep=_support_ep(f, episodes),
                    confidence=float(f.get("confidence", 0.8)),
                )
                if not fact.subject_id or not fact.text:
                    continue
                _, how = self.write_fact(fact)
                stats["facts"] += 1
                if how in stats:
                    stats[how] += 1
            except Exception as e:
                log.warning("写事实失败：%s | %s", f, e)

        if result.get("gist"):
            from_time = episodes[0].event_at if episodes else time.time()
            to_time = episodes[-1].event_at if episodes else time.time()
            try:
                self.write_gist(
                    account_id, 1, from_time, to_time, result["gist"],
                    chat_id=chat_id, source_eps=[e.id for e in episodes],
                )
                stats["gists"] += 1
            except Exception as e:
                log.warning("写摘要失败：%s", e)

        return stats


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _support_time(fact: dict, episodes: list[Episode]) -> float:
    """支撑这条事实的 episode 时间。

    不能直接用 episodes[-1]：事实可能出现在第一条，
    却被记成整批结束的时点。
    """
    idx = fact.get("source_idx")
    if isinstance(idx, int) and 0 <= idx < len(episodes):
        return episodes[idx].event_at

    # 找提到 object 的那条
    obj = fact.get("object") or ""
    if obj:
        for e in episodes:
            if obj in (e.text or ""):
                return e.event_at

    # 兜底用中间那条，不用最后一条
    return episodes[len(episodes) // 2].event_at if episodes else time.time()


def _support_ep(fact: dict, episodes: list[Episode]) -> int | None:
    idx = fact.get("source_idx")
    if isinstance(idx, int) and 0 <= idx < len(episodes):
        return episodes[idx].id
    obj = fact.get("object") or ""
    if obj:
        for e in episodes:
            if obj in (e.text or ""):
                return e.id
    return episodes[len(episodes) // 2].id if episodes else None


def _similarity(a: str, b: str) -> float:
    """两个短句的相似度。

    用字符二元组而不是单字集合：单字集合会把「我住杭州」和「杭州住我」
    判成完全相同，二元组能区分开。
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) < 2 or len(b) < 2:
        return 0.0
    sa = {a[i:i + 2] for i in range(len(a) - 1)}
    sb = {b[i:i + 2] for i in range(len(b) - 1)}
    return len(sa & sb) / len(sa | sb)


def _row_to_fact(row) -> Fact:
    return Fact(
        id=row["id"],
        account_id=row["account_id"],
        subject_type=row["subject_type"],
        subject_id=row["subject_id"],
        predicate=row["predicate"],
        object=row["object"],
        text=row["text"],
        tier=row["tier"],
        valid_from=row["valid_from"],
        valid_until=row["valid_until"],
        recorded_at=row["recorded_at"],
        superseded_by=row["superseded_by"],
        supersedes=row["supersedes"],
        hits=row["hits"] or 0,
        last_hit=row["last_hit"],
        source_chat=row["source_chat"],
        source_ep=row["source_ep"],
        confidence=row["confidence"] or 0.8,
        gone=row["gone"] or 0,
    )
