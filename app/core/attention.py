"""注意筛。

决定一条消息值不值得回。
规则先判，判不出来才调模型。

这一层是整个系统里最省 token 的地方——
80% 的消息在这里被挡掉，不会走到模型。
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import StrEnum

from ..models import ChatType, InboundMessage

log = logging.getLogger(__name__)

class Action(StrEnum):
    REPLY = "reply"        # 回
    IGNORE = "ignore"      # 不回，只记账
    DEFER = "defer"        # 判不出来，交给模型判

@dataclass
class Decision:
    """一条消息的处理决定。"""

    action: Action = Action.IGNORE
    reason: str = ""                 # 为什么这么判（给人看的）
    rule: str = ""                   # 哪条规则命中的
    # 优先级加分（用于多个规则冲突时）
    priority: int = 0
    # 判不出来时给模型看的提示
    hint: str = ""
    # 附加：这条消息的重要度（0-1）
    importance: float = 0.0

@dataclass
class ChatState:
    """注意筛需要的会话状态（外部传进来，不自己查库）。"""

    last_reply_at: float = 0.0        # 上次回复时间
    pending_followup: float = 0.0     # 兼容旧字段：最近一个窗口
    followup_sender: str = ""          # 兼容旧字段：最近一个唤醒者
    followups: dict[str, float] = field(default_factory=dict)  # 每个唤醒者独立截止时间
    recent_senders: list[str] = field(default_factory=list)  # 最近说过话的人
    per_user_count: dict[str, int] = field(default_factory=dict)  # 每人每分钟计数
    minute_key: str = ""
    debounce_token: float = 0.0       # 兼容旧字段
    debounce_tokens: dict[str, float] = field(default_factory=dict)  # 每个发送者独立防抖令牌

class Attention:
    """注意筛。

    规则按优先级从高到低判，命中就返回。
    """

    def __init__(self, settings):
        self.settings = settings
        self._window_counts: dict[str, list[float]] = {}  # chat_id -> 回复时间戳
        # 本次判定里“准备记一笔”的频率配额；真回了才提交
        self._rate_ticket: tuple[str, float] | None = None

    # -- 主入口 ----------------------------------------------------------

    def judge(
        self,
        msg: InboundMessage,
        state: ChatState,
        *,
        is_owner: bool = False,
        last_bot_reply_id: int | None = None,
        bot_names: list[str] | None = None,
        defer_commit: bool = False,
    ) -> Decision:
        """判这条要不要回。

        last_bot_reply_id 是我上一条消息的 episode id，
        用来判断「回复的是不是我」——别人之间互相回复不算叫我。
        """
        text = (msg.text or "").strip()
        now = msg.event_at or time.time()
        self._rate_ticket = None
        self._defer_commit = bool(defer_commit)

        # 1. 总开关
        if not self.settings.get("reply.enabled"):
            return Decision(Action.IGNORE, "回复总开关关着", "reply.enabled")

        # 2. 屏蔽词
        blocked = self._hit_blocklist(text)
        if blocked:
            return Decision(Action.IGNORE, f"含屏蔽词「{blocked}」", "reply.blacklist_words")

        # 接话窗口严格只接唤醒者：其他人的消息只进上下文，不触发回复。
        active_followups = self._active_followups(state, now)
        if (
            msg.chat_type == ChatType.GROUP
            and self.settings.get("reply.followup_sender_only", True)
            and active_followups
            and msg.sender_qq not in active_followups
            and not self._is_called(
                msg, text, last_bot_reply_id=last_bot_reply_id, bot_names=bot_names
            )
        ):
            return Decision(Action.IGNORE, "接话窗口只接唤醒者", "followup_other_sender")

        # 3. 主人：必回，不吃群聊频率；防抖在 pipeline 统一等待
        if is_owner and self.settings.get("reply.owner_always"):
            return Decision(Action.REPLY, "主人", "reply.owner_always", priority=100, importance=1.0)

        # 4. 私聊：必回，不吃群聊频率；防抖在 pipeline 统一等待
        if msg.chat_type == ChatType.PRIVATE:
            if self.settings.get("reply.private_always"):
                return Decision(Action.REPLY, "私聊", "reply.private_always", priority=90, importance=0.8)
            # 私聊不总是回 → 走群那套规则
            return self._group_rules(msg, state, now, is_owner, text, last_bot_reply_id, bot_names)

        # 5. 群聊
        return self._group_rules(msg, state, now, is_owner, text, last_bot_reply_id, bot_names)

    # -- 群聊规则 --------------------------------------------------------

    def _group_rules(
        self, msg: InboundMessage, state: ChatState, now: float, is_owner: bool, text: str,
        last_bot_reply_id: int | None = None,
        bot_names: list[str] | None = None,
    ) -> Decision:
        mode = self.settings.get("reply.group_mode")

        # 被叫到了吗
        called = self._is_called(
            msg, text, last_bot_reply_id=last_bot_reply_id, bot_names=bot_names
        )

        # 唤醒词只在这里开窗；后续普通消息不续期。
        if called:
            self._arm_wake(state, msg, now)
            # 被叫到也要过噪音筛：光喊个名字加个表情，不值得回
            # 但对方在回复我的消息时，短也是回应，不按字数挡
            d = self._check_noise(text, skip_short=bool(msg.reply_to))
            if d:
                d.reason = f"被叫了但{d.reason}"
                d.rule = f"called_{d.rule}"
                return d
            d = self._check_limits(msg, state, now)
            if d:
                return d
            self._commit_rate_if_ready()
            return Decision(Action.REPLY, "被叫到了", "called", priority=80, importance=0.9)

        # 继续接话窗口：窗口只由唤醒词开启，回复和普通消息都不续期
        if self._is_followup(text, msg, state):
            d = self._check_limits(msg, state, now)
            if d:
                return d
            self._commit_rate_if_ready()
            return Decision(Action.REPLY, "接话窗口内", "followup", priority=70, importance=0.6)

        # 按模式判
        if mode == "mention":
            return Decision(Action.IGNORE, "没叫我", "mention_only")

        if mode == "never":
            return Decision(Action.IGNORE, "群聊设为从不主动回", "never")

        if mode == "always":
            d = self._check_noise(text)
            if d:
                return d
            d = self._check_limits(msg, state, now)
            if d:
                return d
            self._commit_rate_if_ready()
            return Decision(Action.REPLY, "设为每条都回", "always", priority=50, importance=0.5)

        if mode == "random":
            # 先过噪音筛
            d = self._check_noise(text)
            if d:
                return d
            d = self._check_limits(msg, state, now)
            if d:
                return d
            prob = self.settings.get("reply.probability") or 0
            if prob <= 0:
                return Decision(Action.IGNORE, "随机概率为 0", "random_zero")
            # 用消息内容做确定性随机，同一条消息判两次结果一致
            if _hash_ratio(msg.chat_id + str(int(msg.event_at))) < prob:
                self._commit_rate_if_ready()
                return Decision(Action.REPLY, f"随机命中（{prob:.0%}）", "random_hit", priority=30, importance=0.4)
            return Decision(Action.IGNORE, f"随机未命中（{prob:.0%}）", "random_miss")

        if mode == "smart":
            d = self._check_noise(text)
            if d:
                return d
            d = self._check_limits(msg, state, now)
            if d:
                return d
            # 判不出来，交给模型
            return Decision(
                Action.DEFER, "需要判相关性", "smart",
                priority=10,
                hint=f"这条群消息要不要接？群名「{msg.group_name or ''}」，内容：{text[:80]}",
                importance=0.4,
            )

        return Decision(Action.IGNORE, f"未知模式 {mode}", "unknown_mode")

    # -- 具体规则 --------------------------------------------------------

    def _is_called(
        self,
        msg: InboundMessage,
        text: str,
        *,
        last_bot_reply_id: int | None = None,
        bot_names: list[str] | None = None,
    ) -> bool:
        """有没有叫到我。

        last_bot_reply_id 是我上一条消息的 episode id。
        只有「回复的是我的消息」才算叫我，别人之间互相回复不算。
        """
        # 戳一戳：默认当唤醒，也可以关掉
        if getattr(msg, "poke", False):
            return bool(self.settings.get("reply.poke_wake", True))

        # @ 了机器人
        bot_qq = self.settings.get("gateway.bot_qq") or ""
        if bot_qq and bot_qq in (msg.at_list or []):
            return True
        if msg.at_list and "all" in msg.at_list:
            return True

        # 回复了我的消息（不是随便回复谁）
        if msg.reply_to and last_bot_reply_id and msg.reply_to == last_bot_reply_id:
            return True

        # 呼语三级判断（名字/昵称/上下文），比单纯子串准一些
        names = [n for n in (bot_names or []) if n]
        if names:
            try:
                from .vocative import VocativeJudge

                judge = VocativeJudge(names)
                res = judge.judge(
                    text,
                    at_list=msg.at_list,
                    bot_qq=bot_qq,
                    reply_to=msg.reply_to,
                    last_bot_reply_id=last_bot_reply_id,
                    is_private=msg.chat_type is ChatType.PRIVATE,
                )
                if res.is_call:
                    return True
            except Exception:  # noqa: BLE001
                pass
            # 人格卡本名/别名直接出现也算叫到；保持原来的唤醒词手感。
            if any(n and n in text for n in names):
                return True

        # 没有激活人格卡时才用旧唤醒词兜底
        words = self.settings.get("reply.wake_words") or []
        return any(w and w in text for w in words)

    def _check_noise(self, text: str, *, skip_short: bool = False) -> Decision | None:
        """噪音筛：表情、单字、纯链接。

        skip_short=True 时跳过「少于 N 字」这条（比如对方在回复我的话，短也是回应）。
        """
        if not text:
            return Decision(Action.IGNORE, "空消息", "empty")

        if self.settings.get("reply.ignore_stickers") and _is_sticker_only(text):
            return Decision(Action.IGNORE, "纯表情", "sticker_only")

        if not skip_short:
            min_chars = self.settings.get("reply.ignore_min_chars") or 0
            if min_chars and _visible_len(text) < min_chars:
                return Decision(Action.IGNORE, f"少于 {min_chars} 字", "too_short")

        if self.settings.get("reply.ignore_links_only") and _is_link_only(text):
            return Decision(Action.IGNORE, "纯链接", "link_only")

        return None

    def _check_limits(self, msg: InboundMessage, state: ChatState, now: float) -> Decision | None:
        return self._check_rate(msg, now)

    def _check_rate(self, msg: InboundMessage, now: float) -> Decision | None:
        """群聊里同一个人在一个时间窗口内最多能被回多少条。"""
        if msg.chat_type != ChatType.GROUP:
            return None
        try:
            window = float(self.settings.get("reply.group_rate_window_sec") or 0)
            limit = int(self.settings.get("reply.group_rate_max_msgs") or 0)
        except Exception:  # noqa: BLE001
            return None
        if window <= 0 or limit <= 0:
            return None
        key = f"{msg.account_id}:{msg.chat_id}:{msg.sender_qq}"
        cutoff = now - window
        entries = [t for t in self._window_counts.get(key, []) if t > cutoff]
        self._window_counts[key] = entries
        if len(entries) >= limit:
            return Decision(
                Action.IGNORE,
                f"这个人 {window:.0f} 秒内已被回 {len(entries)} 条（上限 {limit}）",
                "rate_limit",
            )
        self._rate_ticket = (key, now)
        return None

    def _commit_rate(self) -> None:
        """确认要回了才记一笔。"""
        ticket, self._rate_ticket = self._rate_ticket, None
        if not ticket:
            return
        key, at = ticket
        self._window_counts.setdefault(key, []).append(at)

    def _commit_rate_if_ready(self) -> None:
        """pipeline 延迟提交时，等防抖结束再记账。"""
        if not getattr(self, "_defer_commit", False):
            self._commit_rate()

    def commit_pending_rate(self) -> None:
        """防抖结束后提交这次回复的配额。"""
        self._commit_rate()

    def _arm_wake(self, state: ChatState, msg: InboundMessage, now: float) -> None:
        """只有唤醒词/点名/@ 才开窗；普通消息和回复都不续期。"""
        try:
            seconds = float(self.settings.get("reply.followup_sec") or 0)
        except Exception:  # noqa: BLE001
            seconds = 0
        if seconds > 0:
            deadline = now + seconds
            sender = str(msg.sender_qq or "")
            state.followups[sender] = deadline
            state.pending_followup = deadline
            state.followup_sender = sender
            state.followups = {k: v for k, v in state.followups.items() if v > now}

    def _active_followups(self, state: ChatState, now: float) -> dict[str, float]:
        active = {k: v for k, v in state.followups.items() if v > now}
        if state.pending_followup > now and state.followup_sender:
            active.setdefault(state.followup_sender, state.pending_followup)
        return active

    def _is_followup(self, text: str, msg: InboundMessage, state: ChatState) -> bool:
        """这个发送者自己是否在接话窗口内；每个人窗口独立。"""
        sender = str(msg.sender_qq or "")
        now = msg.event_at or time.time()
        if state.followups.get(sender, 0.0) > now:
            return True
        # 兼容旧字段/旧测试；新逻辑每个人窗口独立，不再要求最近发送者列表。
        if state.followup_sender == sender and state.pending_followup > now:
            return bool(state.recent_senders and sender in state.recent_senders[-10:])
        return False

    def _hit_blocklist(self, text: str) -> str:
        for w in self.settings.get("reply.blacklist_words") or []:
            if w and w in text:
                return w
        return ""

    # -- 重要度 ----------------------------------------------------------

    def score_importance(self, msg: InboundMessage, *, is_owner: bool = False) -> float:
        """给消息打个重要度分（0-1），后续用来决定带多少记忆。"""
        s = 0.3
        text = msg.text or ""

        if is_owner:
            s += 0.4
        if self._is_called(msg, text):
            s += 0.3
        if msg.chat_type == ChatType.PRIVATE:
            s += 0.2
        if "?" in text or "？" in text:
            s += 0.15
        if len(text) > 30:
            s += 0.1
        if _visible_len(text) < 5:
            s -= 0.15

        return max(0.0, min(1.0, s))

    # -- 清理 ------------------------------------------------------------

    def cleanup(self, now: float | None = None) -> None:
        """清掉过期的计数（定期调）。"""
        now = now or time.time()
        try:
            window = float(self.settings.get("reply.group_rate_window_sec") or 60)
        except Exception:  # noqa: BLE001
            window = 60
        cutoff = now - max(60.0, window * 2)
        for key in list(self._window_counts):
            entries = [t for t in self._window_counts[key] if t > cutoff]
            if entries:
                self._window_counts[key] = entries
            else:
                del self._window_counts[key]

# ---------------------------------------------------------------------------
# 文本工具
# ---------------------------------------------------------------------------

_RE_CQ = re.compile(r"\[CQ:[^\]]+\]")
_RE_URL = re.compile(r"https?://\S+|www\.\S+")

def _visible_len(text: str) -> int:
    """去掉 CQ 码、表情标记后的可见字符数。"""
    cleaned = _RE_CQ.sub("", text)
    cleaned = re.sub(r"\[[^\]]{0,10}\]", "", cleaned)
    return len(cleaned.strip())

def _is_sticker_only(text: str) -> bool:
    """纯表情/图片。"""
    cleaned = _RE_CQ.sub("", text).strip()
    if not cleaned:
        return True
    # 只剩方括号标记
    return bool(re.fullmatch(r"(\[[^\]]{0,10}\]\s*)+", cleaned))

def _is_link_only(text: str) -> bool:
    """纯链接。"""
    cleaned = _RE_URL.sub("", text)
    cleaned = _RE_CQ.sub("", cleaned).strip()
    return not cleaned

def _hash_ratio(s: str) -> float:
    """字符串哈希到 0-1，用来做确定性随机。"""
    h = 0
    for c in s:
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
    return h / 0xFFFFFFFF
