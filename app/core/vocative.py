"""呼语判定：属于注意（前额叶）的一部分。

分清三种：
  CALL      在叫我
  MENTION   在跟别人提我
  UNCERTAIN 判不出来

核心原则：判不准就不回。误回的代价比漏回大。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)


# 判定结果，用常量而不是枚举，方便和字符串比较
CALL = "CALL"
MENTION = "MENTION"
UNCERTAIN = "UNCERTAIN"

# 请求/询问的语气
REQUEST_PAT = re.compile(
    r"(帮我|帮忙|看看|看看这个|过来|说一下|说下|问一下|问下|"
    r"在不在|在吗|在么|你觉得|你说|你猜|你会|你能|你能不能|可以吗|行吗|对不对)"
)
QUESTION_PAT = re.compile(r"[?？]|[吗呢]$")

# 第三人称（紧跟名字后面）
THIRD_PERSON_PAT = re.compile(r"^[^\s，。！？]{0,3}(她|他|它|TA|ta)")

# 「跟/和/对 X 说」结构
TALK_ABOUT_PAT = r"[跟和対对向]\s*{name}\s*(说|讲|聊|提|要|问)"

# 讨论名字本身
NAME_META_PAT = r"{name}.?(这名字|三个字|这两个字|怎么念|啥意思|什么意思|好听|难听)"

# 负面/正面评价
NEGATIVE_PAT = re.compile(r"(傻|蠢|废|菜|垃圾|烦人|讨厌|不行|离谱|好笑|丑|没用)")
POSITIVE_PAT = re.compile(r"(喜欢|厉害|好看|可爱|不错|强|牛|赞)")


@dataclass
class MentionResult:
    """判定结果。"""

    verdict: str = UNCERTAIN
    score: int = 0
    hits: list[str] = None       # 命中了哪些信号
    name: str = ""
    position: int = -1
    # 提到我时带情绪？
    emotional: bool = False

    def __post_init__(self):
        if self.hits is None:
            self.hits = []

    @property
    def is_call(self) -> bool:
        return self.verdict == CALL


class VocativeJudge:
    """呼语判定。纯规则，不调模型。"""

    def __init__(self, bot_names: list[str], *, call_threshold: int = 2, mention_threshold: int = -2):
        # 名字长的排前面，避免短名先命中
        self.bot_names = sorted([n for n in bot_names if n], key=len, reverse=True)
        self.call_threshold = call_threshold
        self.mention_threshold = mention_threshold

    # -- 主入口 ----------------------------------------------------------

    def judge(
        self,
        text: str,
        *,
        at_list: list[str] | None = None,
        bot_qq: str = "",
        reply_to: int | None = None,
        last_bot_reply_id: int | None = None,
        last_from_bot: bool = False,
        recently_mentioned: bool = False,
        is_private: bool = False,
    ) -> MentionResult:
        """判这条消息是不是在叫我。

        last_bot_reply_id 是我上一条消息的 episode id。
        只有回复的是「我的」消息才算叫我。
        """
        text = (text or "").strip()

        # @ 了 → 直接 CALL（确定性信号，短路）
        if at_list and bot_qq and bot_qq in at_list:
            return MentionResult(verdict=CALL, score=99, hits=["被 @"], name="@")

        # 回复了我的消息 → CALL。回复别人的，不算
        if reply_to and last_bot_reply_id and reply_to == last_bot_reply_id:
            return MentionResult(verdict=CALL, score=99, hits=["回复我"], name="")

        # 私聊里出现名字就算叫我
        if is_private and self._find_name(text):
            name, pos = self._find_name(text)
            return MentionResult(verdict=CALL, score=5, hits=["私聊"], name=name, position=pos)

        # 找名字
        found = self._find_name(text)
        if not found:
            return MentionResult(verdict=UNCERTAIN, score=0, hits=["没提到"])

        name, pos = found
        name_len = len(name)
        score = 0
        hits: list[str] = []

        # 整句只有名字（去掉标点后）
        stripped = re.sub(r"[\s，。！？、,.!?]", "", text)
        bare_name = len(stripped) <= name_len

        # --- 加分项 ---

        # 句首
        if text.lstrip("　 \t").startswith(name):
            score += 2
            hits.append("句首")

        # 光喊个名字不算叫我（有歧义，可能只是感叹）
        if bare_name:
            score -= 3
            hits.append("只有名字")

        # 名字后面是逗号/感叹号
        after1 = text[pos + name_len:pos + name_len + 1]
        if after1 in ("，", "！", "!", ",", "、"):
            score += 2
            hits.append("后面有停顿")

        # 有第二人称
        if re.search(r"[你您]", text):
            score += 1
            hits.append("有「你」")

        # 问句/请求
        if REQUEST_PAT.search(text):
            score += 2
            hits.append("请求语气")
        elif QUESTION_PAT.search(text):
            score += 1
            hits.append("问句")

        # 上一条是我说的
        if last_from_bot:
            score += 1
            hits.append("上条是我说的")

        # --- 减分项 ---

        # 名字后面紧跟第三人称
        if THIRD_PERSON_PAT.search(text[pos + name_len:pos + name_len + 6]):
            score -= 4
            hits.append("后面跟第三人称")

        # 名字后面是「的/这/那」，或者「说的/提的/讲的」
        after = text[pos + name_len:pos + name_len + 3]
        if after[:1] in ("的", "这", "那"):
            score -= 3
            hits.append("后面是定语")
        elif re.match(r"^[说提讲写][的得]", after):
            score -= 4
            hits.append("后面是「说的」")

        # 「跟/和/对 X 说」
        if re.search(TALK_ABOUT_PAT.format(name=re.escape(name)), text):
            score -= 4
            hits.append("在跟别人说我")

        # 讨论名字本身
        if re.search(NAME_META_PAT.format(name=re.escape(name)), text):
            score -= 4
            hits.append("在讨论名字")

        # 最近别人刚提过我
        if recently_mentioned:
            score -= 2
            hits.append("别人刚提过")

        # 判定
        if score >= self.call_threshold:
            verdict = CALL
        elif score <= self.mention_threshold:
            verdict = MENTION
        else:
            verdict = UNCERTAIN

        # 提到我时的情绪
        emotional = verdict == MENTION and bool(NEGATIVE_PAT.search(text) or POSITIVE_PAT.search(text))

        return MentionResult(
            verdict=verdict, score=score, hits=hits,
            name=name, position=pos, emotional=emotional,
        )

    # -- 内部 ------------------------------------------------------------

    def _find_name(self, text: str) -> tuple[str, int] | None:
        """找名字，返回 (名字, 位置)。"""
        for name in self.bot_names:
            pos = text.find(name)
            if pos >= 0:
                return name, pos
        return None

    def fuzzy_find(self, text: str, max_dist: int = 1) -> tuple[str, int] | None:
        """近似匹配（打错字）。只对 3 字以上的名字做。"""
        for name in self.bot_names:
            if len(name) < 3:
                continue
            for i in range(len(text) - len(name) + 1):
                window = text[i:i + len(name)]
                if _edit_distance(window, name) <= max_dist:
                    return name, i
        return None


def _edit_distance(a: str, b: str) -> int:
    """编辑距离。"""
    if len(a) != len(b):
        return abs(len(a) - len(b)) + 1
    return sum(1 for x, y in zip(a, b, strict=False) if x != y)


def should_watch(text: str) -> bool:
    """提到我时有没有值得注意的情绪。"""
    return bool(NEGATIVE_PAT.search(text) or POSITIVE_PAT.search(text))
