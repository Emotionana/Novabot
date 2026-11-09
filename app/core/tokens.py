"""Token 计数与记录。

只记账，不拦截。
  - 每次模型调用记一条
  - 按环节拆开（人格 / 记忆 / 历史 / 工具）
  - 提供按天、按会话的汇总

token 估算：优先用 tiktoken，没有就退回字符估算。
中文大概 1 token ≈ 1.5 字符，英文 1 token ≈ 4 字符。
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field

log = logging.getLogger(__name__)

# tiktoken 可选
try:
    import tiktoken

    _enc = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return len(_enc.encode(text))

    HAS_TIKTOKEN = True
except ImportError:
    HAS_TIKTOKEN = False

    def count_tokens(text: str) -> int:
        """粗略估算：中文按 1.5 字符/token，其他按 4 字符/token。"""
        if not text:
            return 0
        zh = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        other = len(text) - zh
        return int(zh / 1.5 + other / 4) + 1


@dataclass
class CallRecord:
    """一次模型调用的记录。"""

    label: str = ""            # 干什么用的，如 "reply" / "distill"
    chat_id: str = ""
    account_id: str = ""
    model: str = ""
    n_in: int = 0
    n_out: int = 0
    # 输入拆解
    parts: dict[str, int] = field(default_factory=dict)  # {"persona": 380, "memory": 290, ...}
    # 元数据
    at: float = field(default_factory=time.time)
    duration: float = 0.0
    ok: bool = True
    error: str | None = None

    @property
    def total(self) -> int:
        return self.n_in + self.n_out

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total"] = self.total
        return d


class TokenMeter:
    """记账器。

    内存里存最近的记录，定期落库（或者干脆不落，看日志就够）。
    """

    def __init__(self, keep: int = 1000):
        self.keep = keep
        self._records: list[CallRecord] = []
        self._today: dict[str, int] = defaultdict(int)
        self._day: str = _today_key()

    # -- 记录 ------------------------------------------------------------

    def record(self, rec: CallRecord) -> None:
        self._roll_day()
        self._records.append(rec)
        if len(self._records) > self.keep:
            self._records = self._records[-self.keep :]

        self._today["calls"] += 1
        self._today["in"] += rec.n_in
        self._today["out"] += rec.n_out
        self._today["total"] += rec.total
        if rec.total > self._today.get("max", 0):
            self._today["max"] = rec.total

        if not rec.ok:
            self._today["failed"] += 1

        log.info(
            "[token] chat=%s model=%s in=%d out=%d total=%d %s",
            rec.chat_id or "-",
            rec.model or "-",
            rec.n_in,
            rec.n_out,
            rec.total,
            " ".join(f"{k}={v}" for k, v in rec.parts.items()) or "",
        )

    # -- 汇总 ------------------------------------------------------------

    def today(self) -> dict[str, int]:
        self._roll_day()
        d = dict(self._today)
        if d.get("calls"):
            d["avg"] = d["total"] // d["calls"]
        return d

    def recent(self, n: int = 20) -> list[CallRecord]:
        return self._records[-n:]

    def by_label(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for r in self._records:
            out[r.label] += r.total
        return dict(out)

    def parts_average(self) -> dict[str, int]:
        """各环节平均占多少。"""
        sums: dict[str, int] = defaultdict(int)
        n = 0
        for r in self._records:
            if not r.parts:
                continue
            for k, v in r.parts.items():
                sums[k] += v
            n += 1
        if not n:
            return {}
        return {k: v // n for k, v in sums.items()}

    def snapshot_today(self) -> dict:
        """今天的累计，用于落 meta；跨天自动归零。"""
        self._roll_day()
        out = {"day": self._day}
        out.update({k: int(v) for k, v in self._today.items()})
        return out

    def restore_today(self, data: dict | None) -> None:
        """启动时读回今天的数据；不是同一天就忽略。"""
        if not data or data.get("day") != _today_key():
            return
        self._day = str(data["day"])
        self._today = defaultdict(int)
        for k, v in data.items():
            if k != "day":
                with contextlib.suppress(TypeError, ValueError):
                    self._today[k] = int(v)

    def summary(self) -> dict:
        d = self.today()
        d["by_label"] = self.by_label()
        d["parts_avg"] = self.parts_average()
        return d

    # -- 内部 ------------------------------------------------------------

    def _roll_day(self) -> None:
        key = _today_key()
        if key != self._day:
            self._day = key
            self._today = defaultdict(int)

    def reset(self) -> None:
        self._records.clear()
        self._today = defaultdict(int)


def _today_key() -> str:
    return time.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# 全局实例
# ---------------------------------------------------------------------------

_meter: TokenMeter | None = None


def get_meter() -> TokenMeter:
    global _meter
    if _meter is None:
        _meter = TokenMeter()
    return _meter


def reset_meter(m: TokenMeter | None = None) -> None:
    global _meter
    _meter = m
