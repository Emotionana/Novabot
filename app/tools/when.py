"""把自然语言时间变成时间戳。

只支持几种明确写法，别做"下周三下午"这种玄学解析：
- 2026-11-07 20:30 / 2026-11-07T20:30
- 20:30（今天，过了就明天）
- 明天 20:30 / 后天 20:30
- 10分钟后 / 2小时后 / 3天后
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, tzinfo
from datetime import time as dtime


def parse_when(value: str, *, now: float, tz: tzinfo) -> float:
    text = (value or "").strip()
    if not text:
        raise ValueError("时间是空的")

    base = datetime.fromtimestamp(now, tz)

    # ISO / 日期时间
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=tz)
            return _check(dt.timestamp(), now)
        except ValueError:
            pass

    # 相对时间
    m = re.fullmatch(r"(\d+)\s*(分钟|分|小时|钟头|天)\s*后", text)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        seconds = n * {"分钟": 60, "分": 60, "小时": 3600, "钟头": 3600, "天": 86400}[unit]
        return _check(now + seconds, now)

    # 明天 / 后天 + HH:MM
    m = re.fullmatch(r"(明天|后天)\s*(\d{1,2})[:：](\d{2})", text)
    if m:
        days = 1 if m.group(1) == "明天" else 2
        day = (base + timedelta(days=days)).date()
        dt = datetime.combine(day, dtime(int(m.group(2)), int(m.group(3))), tzinfo=tz)
        return _check(dt.timestamp(), now)

    # 今天 HH:MM；过了就顺延到明天
    m = re.fullmatch(r"(\d{1,2})[:：](\d{2})", text)
    if m:
        dt = datetime.combine(
            base.date(), dtime(int(m.group(1)), int(m.group(2))), tzinfo=tz
        )
        if dt.timestamp() <= now:
            dt += timedelta(days=1)
        return _check(dt.timestamp(), now)

    raise ValueError(f"看不懂时间：{text}（用 20:30 / 明天 20:30 / 10分钟后 这种写法）")


def _check(ts: float, now: float) -> float:
    if ts <= now + 30:
        raise ValueError("提醒时间必须在 30 秒之后")
    if ts > now + 365 * 86400:
        raise ValueError("提醒时间太远了（最多一年）")
    return ts


def format_when(ts: float, tz: tzinfo) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%m-%d %H:%M")
