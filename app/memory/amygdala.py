"""杏仁核：情绪标记。

两层判定：
  第一层 规则快判（同步，微秒级），置信度够就不调模型
  第二层 模型判定（异步，批量），只处理规则判不出的

三个维度：离散标签 + valence（正负）+ arousal（强度）
只有「重要不重要」分不出愤怒和悲伤——一个高唤醒，一个低唤醒。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from ..db import Database
from ..models import Emotion, Episode

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 词表
#
# 只用双字以上的词。单字词误判率太高：
#   "好" 在「好烦」里是程度副词，不是正面情绪
#   "气" 在「天气」里不表情绪
# ---------------------------------------------------------------------------

POSITIVE = (
    "开心", "高兴", "喜欢", "爱你", "好棒", "真棒", "太棒", "棒了", "不错", "厉害",
    "舒服", "优秀", "完美", "期待", "有趣", "好玩", "笑死", "太好了",
    "满足", "幸福", "爽死", "给力", "牛逼", "赞了", "真好", "挺好",
    "可以啊", "行啊", "好耶", "哈哈", "妙啊", "绝了", "服气",
)

NEGATIVE = (
    "烦死", "好烦", "很烦", "累死", "好累", "很累", "心累", "难受",
    "讨厌", "生气", "气死", "崩溃", "完了", "糟糕", "绝望", "痛苦",
    "无聊", "恶心", "吐了", "服了", "无语", "离谱", "废物", "垃圾",
    "算了", "不想", "难过", "伤心", "失望", "委屈", "焦虑", "烦人",
)

INTENSIFIERS = ("太", "超", "巨", "贼", "特别", "非常", "真的", "极其", "爆")

# emoji / 表情码 → (emotion, valence, arousal)
EMOJI_MAP = {
    "笑": ("joy", 0.6, 0.6),
    "哭": ("sadness", -0.7, 0.5),
    "怒": ("anger", -0.7, 0.8),
    "汗": ("neutral", -0.1, 0.3),
    "捂脸": ("neutral", -0.2, 0.4),
    "裂开": ("sadness", -0.6, 0.7),
    "白眼": ("disgust", -0.5, 0.5),
    "委屈": ("sadness", -0.6, 0.4),
    "吃瓜": ("neutral", 0.1, 0.3),
    "赞": ("joy", 0.6, 0.4),
    "ok": ("joy", 0.4, 0.3),
    "抱拳": ("neutral", 0.2, 0.3),
    "摸鱼": ("neutral", 0.2, 0.2),
    "偷笑": ("joy", 0.5, 0.4),
    "呲牙": ("joy", 0.5, 0.5),
    "斜眼笑": ("joy", 0.4, 0.5),
}


@dataclass
class Judgment:
    """一次判定的结果。"""

    emotion: str = "neutral"
    valence: float = 0.0
    arousal: float = 0.3
    confidence: float = 0.2
    source: str = "rule"

    @property
    def sure(self) -> bool:
        return self.confidence >= 0.6


class Amygdala:
    def __init__(self, db: Database, settings=None):
        self.db = db
        self.settings = settings
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        # schema.sql 里已有，这里只是兜底
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS emotions (
              id          INTEGER PRIMARY KEY,
              episode_id  INTEGER NOT NULL,
              sender_qq   TEXT NOT NULL,
              chat_id     TEXT NOT NULL,
              emotion     TEXT DEFAULT 'neutral',
              valence     REAL DEFAULT 0,
              arousal     REAL DEFAULT 0.3,
              confidence  REAL DEFAULT 0.3,
              source      TEXT DEFAULT 'rule',
              created_at  REAL NOT NULL
            )
            """
        )

    # ==================================================================
    # 规则快判
    # ==================================================================

    def rule_judge(self, text: str) -> Judgment:
        """规则判情绪。

        走词典扫描：2.2 万词 + 手写补充，处理否定和程度副词。
        """
        text = text or ""
        if not text.strip():
            return Judgment("neutral", 0.0, 0.1, 0.1, "rule")

        # 全是问号算好奇，不算情绪
        if text.strip("?？ ") == "":
            return Judgment("neutral", 0.0, 0.4, 0.3, "rule")

        from .lexicon import get_lexicon

        lex = get_lexicon()
        hits = lex.scan(text)

        # 表情优先（一个笑哭比一整句话更能说明情绪）
        for key, (emo, val, aro) in EMOJI_MAP.items():
            if key in text:
                total_v = sum(h[1] for h in hits) if hits else 0.0
                # 表情和文字一致就用表情的标签
                if not hits or (total_v > 0) == (val > 0):
                    return Judgment(emo, val, aro, 0.6, "rule")

        if not hits:
            # 没有情绪词，但可能有标点信号
            boost = _punct_boost(text)
            if boost > 1.2:
                return Judgment("neutral", 0.0, min(0.3 + 0.2 * boost, 0.8), 0.25, "rule")
            return Judgment("neutral", 0.0, 0.3, 0.2, "rule")

        # 汇总
        total_v = sum(h[1] for h in hits)
        # 多词时取平均强度，单词直接用
        avg_a = sum(h[2] for h in hits) / len(hits)
        max_abs = max(abs(h[1]) for h in hits)

        # 标点加成
        boost = _punct_boost(text)

        valence = max(-1.0, min(1.0, total_v * min(boost, 1.4)))
        arousal = min(avg_a * boost, 1.0)

        # 情绪标签
        if valence > 0.15:
            emotion = "joy"
        elif valence < -0.15:
            # 高强度负 → 愤怒；低强度负 → 悲伤
            emotion = "anger" if (arousal > 0.55 and max_abs > 0.5) else "sadness"
        else:
            emotion = "neutral"

        # 置信度：词越多、越强越自信
        confidence = min(0.4 + len(hits) * 0.08 + max_abs * 0.25, 0.85)

        return Judgment(emotion, valence, arousal, confidence, "rule")

    # ==================================================================
    # 模型判定
    # ==================================================================

    JUDGE_PROMPT = """判断下面每句话的情绪。

输出 JSON 数组，每项：
{"i": 序号, "emotion": "joy|anger|sadness|fear|surprise|disgust|neutral",
 "valence": -1到1, "arousal": 0到1}

说明：
- valence 是正负，正=好，负=坏
- arousal 是强度，0=平静，1=激动
- 讽刺（"真好啊"其实是抱怨）按真实意思判
- 判不出来给 neutral，valence 0，arousal 0.3
- 只输出 JSON，不要解释

句子：
%s"""

    async def model_judge(self, llm, texts: list[str]) -> list[Judgment]:
        """调模型批量判。一次最多 20 条。"""
        if not texts:
            return []

        numbered = "\n".join(f"{i}. {t[:100]}" for i, t in enumerate(texts))
        prompt = self.JUDGE_PROMPT % numbered

        try:
            resp = await llm.chat(
                system="你是一个情绪分析器。只输出 JSON，不解释。",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=800,
                temperature=0,
            )
            data = _parse_json(resp.text if hasattr(resp, "text") else str(resp))
            if not isinstance(data, list):
                raise ValueError("返回的不是数组")

            out: list[Judgment] = []
            for item in data:
                out.append(
                    Judgment(
                        emotion=item.get("emotion", "neutral"),
                        valence=float(item.get("valence", 0)),
                        arousal=float(item.get("arousal", 0.3)),
                        confidence=0.8,
                        source="model",
                    )
                )
            # 条数对不上就补 neutral
            while len(out) < len(texts):
                out.append(Judgment("neutral", 0.0, 0.3, 0.3, "model"))
            return out[: len(texts)]
        except Exception as e:
            log.warning("模型判情绪失败：%s", e)
            return [Judgment("neutral", 0.0, 0.3, 0.1, "model") for _ in texts]

    # ==================================================================
    # 融合
    # ==================================================================

    def fuse(self, rule: Judgment, model: Judgment) -> Judgment:
        """规则和模型融合。

        取保守的那个：置信度取低，强度取平均，标签不一致时给 neutral。
        把「今天好累」判成重度悲伤比判成中性更糟。
        """
        if rule.emotion != model.emotion:
            # 不一致 → 中性，但保留强度
            return Judgment(
                emotion="neutral",
                valence=(rule.valence + model.valence) / 2,
                arousal=(rule.arousal + model.arousal) / 2,
                confidence=min(rule.confidence, model.confidence),
                source="fused",
            )
        return Judgment(
            emotion=rule.emotion,
            valence=(rule.valence + model.valence) / 2,
            arousal=(rule.arousal + model.arousal) / 2,
            confidence=min(rule.confidence, model.confidence),
            source="fused",
        )

    # ==================================================================
    # 落库
    # ==================================================================

    def judge_episode(self, ep: Episode) -> Judgment:
        """判一条 episode（只走规则，同步）。"""
        j = self.rule_judge(ep.text or "")
        self._save(ep.id, ep.sender_qq, ep.chat_id, j)
        return j

    def judge_many(self, eps: list[Episode]) -> list[Judgment]:
        """批量判，只走规则。"""
        out = []
        for ep in eps:
            out.append(self.judge_episode(ep))
        return out

    def _save(self, ep_id: int, qq: str, chat_id: str, j: Judgment) -> None:
        # 已有就更新
        existing = self.db.query_one(
            "SELECT id FROM emotions WHERE episode_id = ?", [ep_id]
        )
        if existing:
            self.db.execute(
                "UPDATE emotions SET emotion=?, valence=?, arousal=?, confidence=?, source=? WHERE id=?",
                [j.emotion, j.valence, j.arousal, j.confidence, j.source, existing["id"]],
            )
        else:
            self.db.execute(
                "INSERT INTO emotions (episode_id, sender_qq, chat_id, emotion, valence, arousal, confidence, source, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                [ep_id, qq, chat_id, j.emotion, j.valence, j.arousal, j.confidence, j.source, time.time()],
            )

    def of_episode(self, ep_id: int) -> Emotion | None:
        row = self.db.query_one("SELECT * FROM emotions WHERE episode_id = ?", [ep_id])
        if row is None:
            return None
        return Emotion(
            id=row["id"], episode_id=row["episode_id"], sender_qq=row["sender_qq"],
            chat_id=row["chat_id"], emotion=row["emotion"], valence=row["valence"],
            arousal=row["arousal"], confidence=row["confidence"],
            source=row["source"], created_at=row["created_at"],
        )

    # ==================================================================
    # 画像与基线
    # ==================================================================

    def baseline(self, qq: str, days: int = 30) -> tuple[float, float, int]:
        """这个人的情绪常态：(平均 valence, 平均 arousal, 样本数)。"""
        row = self.db.query_one(
            """
            SELECT AVG(valence) AS v, AVG(arousal) AS a, COUNT(*) AS n
            FROM emotions
            WHERE sender_qq = ? AND created_at > ?
            """,
            [qq, time.time() - days * 86400],
        )
        if row is None or not row["n"]:
            return 0.0, 0.3, 0
        return row["v"] or 0.0, row["a"] or 0.3, row["n"]

    def update_baseline(self, qq: str, days: int = 30) -> None:
        """更新基线表。每天跑一次。"""
        v, a, n = self.baseline(qq, days)
        if n < 10:
            return
        self.db.execute(
            "INSERT OR REPLACE INTO emotion_baseline (sender_qq, avg_valence, avg_arousal, sample_count, updated_at) "
            "VALUES (?,?,?,?,?)",
            [qq, v, a, n, time.time()],
        )

    def mood_shift(self, qq: str, days: int = 7) -> float:
        """跟基线比偏了多少。-1 到 1，负=比平时差。"""
        row = self.db.query_one(
            "SELECT avg_valence FROM emotion_baseline WHERE sender_qq = ?", [qq]
        )
        if row is None:
            return 0.0
        base_v = row["avg_valence"] or 0.0

        recent = self.db.query_one(
            "SELECT AVG(valence) AS v FROM emotions WHERE sender_qq = ? AND created_at > ?",
            [qq, time.time() - days * 86400],
        )
        if recent is None or recent["v"] is None:
            return 0.0
        return max(-1.0, min(1.0, recent["v"] - base_v))

    def profile(self, qq: str, days: int = 30) -> dict:
        """情绪画像。"""
        rows = self.db.query(
            """
            SELECT emotion, COUNT(*) AS n FROM emotions
            WHERE sender_qq = ? AND created_at > ?
            GROUP BY emotion ORDER BY n DESC
            """,
            [qq, time.time() - days * 86400],
        )
        total = sum(r["n"] for r in rows) or 1
        return {
            "distribution": {r["emotion"]: round(r["n"] / total, 3) for r in rows},
            "sample": total,
            "shift": round(self.mood_shift(qq, days), 3),
        }


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _parse_json(text: str):
    """从模型输出里抠 JSON。"""
    text = text.strip()
    # 去掉 markdown 代码块
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    # 找第一个 [ 或 {
    for i, c in enumerate(text):
        if c in "[{":
            text = text[i:]
            break
    return json.loads(text)


def _punct_boost(text: str) -> float:
    """标点加成：感叹号、省略号会加强情绪。"""
    boost = 1.0
    if "！" in text or "!" in text:
        boost *= 1.25
    if "……" in text or "..." in text:
        boost *= 1.1
    if "？？" in text or "??" in text:
        boost *= 1.15
    return boost
