"""情绪词典。

数据来自大连理工情感词汇本体（2.7 万词），转成紧凑 JSON。
另外保留一份手写补充——词典里没有的网络用语和口语。

加载一次常驻内存，查询 O(1)。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

DICT_DIR = Path(__file__).resolve().parents[2] / "data" / "dict"
DICT_FILE = DICT_DIR / "emotion_words.json"


# ---------------------------------------------------------------------------
# 手写补充
#
# 词典里查不到的口语、网络用语、常见短语。
# 格式：词 → (valence, arousal)
# ---------------------------------------------------------------------------

SUPPLEMENT: dict[str, tuple[float, float]] = {
    # 负面
    "难过": (-0.55, 0.35),
    "伤心": (-0.65, 0.40),
    "难受": (-0.55, 0.40),
    "心累": (-0.60, 0.30),
    "好累": (-0.45, 0.25),
    "很累": (-0.45, 0.25),
    "累死": (-0.70, 0.55),
    "烦死": (-0.75, 0.75),
    "好烦": (-0.55, 0.55),
    "很烦": (-0.55, 0.55),
    "烦人": (-0.50, 0.50),
    "崩溃": (-0.85, 0.85),
    "绝望": (-0.90, 0.60),
    "失望": (-0.60, 0.35),
    "委屈": (-0.60, 0.40),
    "焦虑": (-0.55, 0.60),
    "生气": (-0.60, 0.70),
    "气死": (-0.80, 0.85),
    "无语": (-0.40, 0.30),
    "离谱": (-0.45, 0.45),
    "服了": (-0.40, 0.35),
    "算了": (-0.25, 0.15),
    "不想": (-0.30, 0.20),
    "蚌埠住": (-0.40, 0.55),
    "绷不住": (-0.40, 0.55),
    "崩不住": (-0.40, 0.55),
    "麻了": (-0.45, 0.30),
    "破防": (-0.70, 0.70),
    "心态崩": (-0.80, 0.75),
    "要死": (-0.60, 0.60),
    "救命": (-0.50, 0.65),
    "吐了": (-0.60, 0.60),
    "恶心": (-0.65, 0.55),
    "垃圾": (-0.60, 0.50),
    "废物": (-0.65, 0.50),
    "糟糕": (-0.55, 0.45),
    "倒霉": (-0.55, 0.40),
    "可惜": (-0.35, 0.25),
    "遗憾": (-0.40, 0.25),
    "白干": (-0.45, 0.30),
    "白费": (-0.50, 0.30),
    "完蛋": (-0.70, 0.65),
    "坏了": (-0.50, 0.45),
    "糟了": (-0.55, 0.55),
    "唉": (-0.20, 0.10),
    "呵呵": (-0.30, 0.25),
    "绝了": (-0.10, 0.40),      # 看语境，默认略负
    "就这": (-0.35, 0.30),
    "摆烂": (-0.45, 0.25),
    "躺平": (-0.30, 0.15),
    "emo": (-0.55, 0.30),
    # 正面
    "开心": (0.60, 0.60),
    "高兴": (0.55, 0.55),
    "喜欢": (0.60, 0.50),
    "爱你": (0.75, 0.60),
    "好棒": (0.65, 0.60),
    "真棒": (0.65, 0.60),
    "太棒": (0.75, 0.75),
    "不错": (0.45, 0.35),
    "厉害": (0.55, 0.50),
    "优秀": (0.60, 0.45),
    "完美": (0.70, 0.60),
    "舒服": (0.50, 0.30),
    "爽": (0.65, 0.60),
    "爽死": (0.80, 0.80),
    "给力": (0.60, 0.55),
    "牛": (0.55, 0.50),
    "牛逼": (0.70, 0.65),
    "哈哈": (0.55, 0.55),
    "笑死": (0.65, 0.70),
    "好耶": (0.70, 0.70),
    "可以啊": (0.40, 0.35),
    "行啊": (0.35, 0.30),
    "期待": (0.50, 0.45),
    "有趣": (0.45, 0.40),
    "好玩": (0.50, 0.45),
    "妙啊": (0.55, 0.45),
    "服气": (0.30, 0.30),
    "谢谢": (0.45, 0.30),
    "辛苦了": (0.40, 0.25),
    "mua": (0.70, 0.55),
}


# 否定词：出现在情感词前面要翻转极性
NEGATIONS = ("不", "没", "别", "无", "非", "未", "莫", "勿", "甭")

# 程度副词：修饰后面情感词的强度
DEGREE = {
    "超级": 1.5, "太过": 1.4, "太": 1.4, "超": 1.4, "巨": 1.5, "贼": 1.4, "特别": 1.3,
    "非常": 1.4, "真的": 1.2, "极其": 1.6, "极度": 1.6,
    "有点": 0.6, "稍微": 0.5, "略微": 0.5, "一点": 0.6,
    "还": 0.8, "挺": 1.1, "比较": 0.9,
}


class EmotionLexicon:
    """情绪词典。

    优先查手写补充（口语更准），再查大词典。
    """

    def __init__(self, dict_file: Path | None = None):
        self.words: dict[str, tuple[float, float]] = dict(SUPPLEMENT)
        self.source = "supplement"
        self._load_big(dict_file or DICT_FILE)

    def _load_big(self, path: Path) -> None:
        if not path.is_file():
            log.warning("大词典不存在：%s（只用手写补充）", path)
            return
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
            # 手写的优先，不覆盖
            for w, v in data.items():
                if w not in self.words:
                    self.words[w] = (float(v[0]), float(v[1]))
            self.source = f"supplement + {len(data)} 词"
            log.info("情绪词典加载：%d 词（%s）", len(self.words), path.name)
        except Exception as e:
            log.warning("加载大词典失败：%s", e)

    def __len__(self) -> int:
        return len(self.words)

    def get(self, word: str) -> tuple[float, float] | None:
        return self.words.get(word)

    # ------------------------------------------------------------------
    # 文本扫描
    # ------------------------------------------------------------------

    def scan(self, text: str, *, max_len: int = 6) -> list[tuple[str, float, float, int]]:
        """扫一遍文本，找出所有情绪词。

        返回 [(词, valence, arousal, 位置)]，长的词优先匹配。
        会处理否定和程度副词。
        """
        if not text:
            return []

        hits: list[tuple[str, float, float, int]] = []
        used: set[int] = set()   # 已被匹配的位置
        i = 0
        n = len(text)

        while i < n:
            if i in used:
                i += 1
                continue

            matched = None
            # 从长到短试
            for length in range(min(max_len, n - i), 0, -1):
                cand = text[i:i + length]
                if len(cand) < 2:
                    continue
                if cand in self.words:
                    matched = (cand, length)
                    break

            if matched is None:
                i += 1
                continue

            word, length = matched
            valence, arousal = self.words[word]

            # 检查前面的否定词（最多往前看 3 字）
            prefix = text[max(0, i - 3):i]
            negated = any(prefix.endswith(neg) for neg in NEGATIONS)
            if negated:
                valence = -valence * 0.7   # 否定后翻转，但强度打折
                arousal = arousal * 0.9

            # 检查前面的程度副词
            for adv, factor in DEGREE.items():
                if prefix.endswith(adv):
                    valence *= factor
                    arousal = min(arousal * factor, 1.0)
                    break

            valence = max(-1.0, min(1.0, valence))
            arousal = max(0.0, min(1.0, arousal))

            hits.append((word, valence, arousal, i))
            for k in range(i, i + length):
                used.add(k)
            i += length

        return hits


# ---------------------------------------------------------------------------
# 全局实例
# ---------------------------------------------------------------------------

_lexicon: EmotionLexicon | None = None


def get_lexicon() -> EmotionLexicon:
    global _lexicon
    if _lexicon is None:
        _lexicon = EmotionLexicon()
    return _lexicon
