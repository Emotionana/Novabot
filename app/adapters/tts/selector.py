"""语音参考音选择。

核心：会话内粘住一条参考音，不要每次都换。

按权重随机挑看着公平，实际听着像几个人轮着说话——
同一个人连着三条消息三个音色，耳朵立刻听出来不对。

粘住策略：
  第一次随机选一条 → 这个会话后续都用它
  只有条件明显变化时才切：情绪大变、长度不符、粘够久
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass

from ...db import Database
from ...models import RuntimeState, VoiceRef

log = logging.getLogger(__name__)


@dataclass
class Selection:
    """一次选参考音的结果。"""

    ref: VoiceRef | None = None
    reason: str = ""
    switched: bool = False        # 是不是换了（从上次不同）
    previous_id: str = ""


class VoiceSelector:
    """参考音选择器。

    粘住状态存在内存里（会话级），重启后重新选。
    """

    def __init__(self, db: Database, settings):
        self.db = db
        self.settings = settings
        # chat_id -> (ref_id, 选定时间, 当时情绪)
        self._sticky: dict[str, tuple[str, float, str]] = {}

    # -- 主入口 ----------------------------------------------------------

    def select(
        self,
        persona_id: str,
        chat_id: str,
        text: str,
        *,
        emotion: str | None = None,
        state: RuntimeState | None = None,
        traits: dict | None = None,
        style=None,
        account_id: str = "",
    ) -> Selection:
        """给这个会话选一条参考音。

        长度、情绪、人格状态都是"当前情况"，合起来决定用哪条。
        """
        account_id = account_id or str(getattr(self.settings, "account_id", "") or "")
        refs = self._load_refs(persona_id, account_id)
        if not refs:
            return Selection(reason="这个角色没有可用的参考音")

        key = self._key(account_id, chat_id)
        prev = self._sticky.get(key)
        prev_ref = next((r for r in refs if prev and r.id == prev[0]), None)

        # 1. 有粘住的，先看还能不能用
        if prev_ref:
            keep, why = self._can_keep(prev_ref, text, emotion, key)
            if keep:
                return Selection(ref=prev_ref, reason=why, previous_id=prev_ref.id)
            switch_why = why
        else:
            switch_why = "第一次选"

        # 2. 挑一条新的
        candidates = self._filter(refs, text, emotion, state, traits, style)
        if not candidates:
            # 全被筛掉就退回不过滤，宁可音色不合适也别发不出声
            candidates = refs

        chosen = self._pick(candidates)
        self._sticky[key] = (chosen.id, time.time(), emotion or "")
        return Selection(
            ref=chosen,
            reason=f"{switch_why}，选了「{chosen.name}」",
            switched=prev_ref is not None,
            previous_id=prev_ref.id if prev_ref else "",
        )

    @staticmethod
    def _key(account_id: str, chat_id: str) -> str:
        # 多开：同一个群在两个账号下是两段独立状态；没账号时保持老 key 形态
        return f"{account_id}:{chat_id}" if account_id else chat_id

    # -- 粘住判定 --------------------------------------------------------

    def _can_keep(
        self, ref: VoiceRef, text: str, emotion: str | None, chat_id: str
    ) -> tuple[bool, str]:
        """粘住的这条还能不能用。

        chat_id 必须由调用方传进来——之前靠 ref 反查 chat_id，
        多个会话共用同一条参考音时会拿到别人的计时。
        """
        # 长度不在范围内 → 必须换
        n = _visible_len(text)
        if not (ref.min_chars <= n <= ref.max_chars):
            return False, f"文本 {n} 字不在「{ref.name}」的适用范围内"

        # 情绪明显不符 → 换（除非配置关掉了"情绪变了就换"）
        if (
            ref.emotions
            and emotion
            and emotion not in ref.emotions
            and self.settings.get("voice.sticky_on_emotion") is not False
        ):
            return False, f"情绪 {emotion} 不在「{ref.name}」的适用范围"

        # 粘够久了，换一条透透气（默认 30 分钟）
        max_sticky = self.settings.get("voice.sticky_max_sec") or 1800
        entry = self._sticky.get(chat_id)
        if entry:
            age = time.time() - entry[1]
            if max_sticky and age > max_sticky:
                return False, f"「{ref.name}」用了 {age/60:.0f} 分钟，换一条"

        return True, f"继续用「{ref.name}」"

    # -- 筛选 ------------------------------------------------------------

    def _filter(
        self,
        refs: list[VoiceRef],
        text: str,
        emotion: str | None,
        state: RuntimeState | None,
        traits: dict | None = None,
        style=None,
    ) -> list[VoiceRef]:
        """按长度、情绪、人格状态筛。"""
        out = list(refs)

        # 长度
        n = _visible_len(text)
        by_len = [r for r in out if r.min_chars <= n <= r.max_chars]
        if by_len:
            out = by_len

        # 标签：文本是疑问/感叹时，优先用打了这个标签的参考音
        tags = _text_tags(text)
        if tags:
            tagged = [r for r in out if set(r.tags or []) & tags]
            if tagged:
                out = tagged

        # 情绪：优先挑「明确支持这个情绪」的，没有才退到不挑情绪的通用参考
        if emotion:
            exact = [r for r in out if r.emotions and emotion in r.emotions]
            if exact:
                out = exact
            else:
                allowed = [r for r in out if not r.emotions or emotion in r.emotions]
                if allowed:
                    out = allowed

        # 人格状态：优先挑「条件命中」的，没有才退到无条件的兜底
        if state or traits or style:
            matched = [
                r for r in out
                if r.condition
                and _eval_condition(r.condition, state=state, traits=traits, style=style)
            ]
            if matched:
                out = matched
            else:
                plain = [r for r in out if not r.condition]
                if plain:
                    out = plain

        return out

    def _pick(self, refs: list[VoiceRef]) -> VoiceRef:
        """按权重挑一条。"""
        total = sum(max(r.weight, 0.01) for r in refs)
        x = random.uniform(0, total)
        acc = 0.0
        for r in refs:
            acc += max(r.weight, 0.01)
            if x <= acc:
                return r
        return refs[-1]

    # -- 数据 ------------------------------------------------------------

    def _load_refs(self, persona_id: str, account_id: str = "") -> list[VoiceRef]:
        rows = self.db.query(
            "SELECT * FROM voice_refs WHERE enabled = 1 AND account_id = ? "
            "AND (persona_id = ? OR persona_id = '') ORDER BY weight DESC",
            [account_id, persona_id],
        )
        out = []
        for r in rows:
            out.append(
                VoiceRef(
                    id=r["id"],
                    account_id=r["account_id"] or "",
                    persona_id=r["persona_id"],
                    name=r["name"],
                    engine=r["engine"],
                    audio_path=r["audio_path"],
                    duration=r["duration"] or 0.0,
                    prompt_text=r["prompt_text"] or "",
                    params=json.loads(r["params"] or "{}"),
                    weight=r["weight"] or 1.0,
                    tags=json.loads(r["tags"] or "[]"),
                    min_chars=r["min_chars"] or 0,
                    max_chars=r["max_chars"] or 9999,
                    emotions=json.loads(r["emotions"] or "[]"),
                    condition=json.loads(r["condition"]) if r["condition"] else None,
                    enabled=True,
                )
            )
        return out

    # -- 状态 ------------------------------------------------------------

    def current(self, chat_id: str, *, account_id: str = "") -> str:
        """这个会话现在用的哪条。"""
        entry = self._sticky.get(self._key(account_id, chat_id))
        return entry[0] if entry else ""

    def reset(self, chat_id: str | None = None, *, account_id: str = "") -> None:
        """清掉粘住状态。"""
        if chat_id:
            self._sticky.pop(self._key(account_id, chat_id), None)
        else:
            self._sticky.clear()


def _visible_len(text: str) -> int:
    import re

    cleaned = re.sub(r"\[CQ:[^\]]+\]", "", text or "")
    cleaned = re.sub(r"\[[^\]]{0,10}\]", "", cleaned)
    return len(cleaned.strip())


def _condition_context(
    state: RuntimeState | None = None,
    traits: dict | None = None,
    style=None,
) -> dict:
    """把人格快照摊平成 {"state.mood": -0.4, "trait.傲娇": 0.8, ...}。"""
    ctx: dict[str, float] = {}
    if state is not None:
        for field in ("mood", "energy", "patience", "affection", "closeness"):
            ctx[f"state.{field}"] = float(getattr(state, field, 0.0))
    for name, weight in (traits or {}).items():
        ctx[f"trait.{name}"] = float(weight)
    if style is not None:
        for field in ("sass", "emoji_level"):
            if hasattr(style, field):
                ctx[f"style.{field}"] = float(getattr(style, field))
        if hasattr(style, "emoji_level"):
            ctx["style.emoji"] = float(style.emoji_level)  # 设计文档里的写法
        length = getattr(style, "length", "")
        if length:
            # 长度是枚举，映射成 0/0.5/1 方便写条件
            ctx["style.length"] = {"short": 0.0, "medium": 0.5, "long": 1.0}.get(length, 0.5)
    return ctx


def _eval_condition(
    cond: dict | None,
    state: RuntimeState | None = None,
    *,
    traits: dict | None = None,
    style=None,
    ctx: dict | None = None,
) -> bool:
    """判人格条件。支持 state.* / trait.* / style.*，以及 all / any / not。"""
    if not cond:
        return True
    if "all" in cond:
        return all(_eval_condition(c, state, traits=traits, style=style, ctx=ctx) for c in cond["all"])
    if "any" in cond:
        return any(_eval_condition(c, state, traits=traits, style=style, ctx=ctx) for c in cond["any"])
    if "not" in cond:
        return not _eval_condition(cond["not"], state, traits=traits, style=style, ctx=ctx)

    ctx = ctx if ctx is not None else _condition_context(state, traits, style)
    for field, expr in cond.items():
        actual = ctx.get(field)
        if actual is None:
            return False
        if not _compare(actual, str(expr)):
            return False
    return True


def _compare(actual: float, expr: str) -> bool:
    expr = expr.strip()
    for op in (">=", "<=", "==", "!=", ">", "<"):
        if expr.startswith(op):
            try:
                target = float(expr[len(op):].strip())
            except ValueError:
                return False
            return {
                ">=": actual >= target,
                "<=": actual <= target,
                "==": abs(actual - target) < 1e-6,
                "!=": abs(actual - target) >= 1e-6,
                ">": actual > target,
                "<": actual < target,
            }[op]
    return False


def _text_tags(text: str) -> set[str]:
    """从回复文本里抠出可匹配的标签。只做疑问/感叹两种，别过度设计。"""
    import re

    text = text or ""
    tags: set[str] = set()
    if re.search(r"[?？]|吗|呢|么$", text):
        tags.add("疑问")
    if re.search(r"[!！]|太.{0,4}了|真.{0,4}", text):
        tags.add("感叹")
    return tags
