"""主动私聊：从真实消息里学这个人的作息和关系。

只做"算"，不做"发"。纯函数为主，方便测试。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo


@dataclass(slots=True)
class HabitProfile:
    qq: str
    name: str
    is_owner: bool
    msg_count: int          # people 表里的历史总数
    total: int              # 统计窗口内的消息数
    active_days: int
    first_seen: float
    last_seen: float
    hours: list[int]        # 24 桶（全部）
    peak_hours: list[int]
    avg_per_day: float
    hours_weekday: list[int] = field(default_factory=lambda: [0] * 24)
    hours_weekend: list[int] = field(default_factory=lambda: [0] * 24)
    peak_weekday: list[int] = field(default_factory=list)
    peak_weekend: list[int] = field(default_factory=list)

    def peaks_for(self, now: float, tz) -> list[int]:
        """今天该看工作日还是周末的峰值。"""
        weekend = datetime.fromtimestamp(now, tz).weekday() >= 5
        return (self.peak_weekend if weekend else self.peak_weekday) or self.peak_hours


def local_hour(ts: float, tz: tzinfo) -> int:
    return datetime.fromtimestamp(ts, tz).hour


def local_day(ts: float, tz: tzinfo) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d")


def day_bounds(ts: float, tz: tzinfo) -> tuple[float, float]:
    """这个时间所在自然日的 [00:00, 次日 00:00) 时间戳。"""
    d = datetime.fromtimestamp(ts, tz).replace(hour=0, minute=0, second=0, microsecond=0)
    start = d.timestamp()
    return start, (d + timedelta(days=1)).timestamp()


def is_quiet(ts: float, start_hour: int, end_hour: int, tz: tzinfo) -> bool:
    """跨午夜的安静时段：23 点到次日 8 点。start == end 表示不启用。"""
    if start_hour == end_hour:
        return False
    h = local_hour(ts, tz)
    if start_hour < end_hour:
        return start_hour <= h < end_hour
    return h >= start_hour or h < end_hour


def load_habit(
    db,
    account_id: str,
    qq: str,
    *,
    now: float,
    tz: tzinfo,
    days: int = 30,
    min_messages: int = 1,
) -> HabitProfile | None:
    """按最近 N 天的消息算作息。消息太少返回 None。"""
    person = db.query_one(
        "SELECT qq, name, alias, msg_count, first_seen, last_seen, is_owner "
        "FROM people WHERE account_id = ? AND qq = ? AND gone = 0",
        [account_id, qq],
    )
    if person is None:
        return None

    since = now - days * 86400
    rows = db.query(
        "SELECT event_at FROM episodes "
        "WHERE account_id = ? AND sender_qq = ? AND sender_qq <> 'self' "
        "AND gone = 0 AND event_at >= ? ORDER BY event_at DESC LIMIT 5000",
        [account_id, qq, since],
    )
    if len(rows) < min_messages:
        return None

    hours = [0] * 24
    wd_hours = [0] * 24
    we_hours = [0] * 24
    dayset: set[str] = set()
    last = 0.0
    for r in rows:
        ts = float(r["event_at"])
        dt = datetime.fromtimestamp(ts, tz)
        hours[dt.hour] += 1
        (we_hours if dt.weekday() >= 5 else wd_hours)[dt.hour] += 1
        dayset.add(local_day(ts, tz))
        last = max(last, ts)

    def _peaks(hist: list[int]) -> list[int]:
        top = sorted(range(24), key=lambda i: hist[i], reverse=True)[:3]
        return [h for h in top if hist[h] > 0]

    peak = _peaks(hours) or [local_hour(last, tz)]
    active_days = max(1, len(dayset))

    name = person["alias"] or person["name"] or qq
    return HabitProfile(
        qq=qq,
        name=name,
        is_owner=bool(person["is_owner"]),
        msg_count=int(person["msg_count"] or 0),
        total=len(rows),
        active_days=active_days,
        first_seen=float(person["first_seen"] or now),
        last_seen=last,
        hours=hours,
        peak_hours=peak,
        avg_per_day=len(rows) / active_days,
        hours_weekday=wd_hours,
        hours_weekend=we_hours,
        peak_weekday=_peaks(wd_hours),
        peak_weekend=_peaks(we_hours),
    )


def habit_fit(now: float, peak_hours: list[int], tz: tzinfo) -> float:
    """现在离他平时活跃的时间有多近（0-1）。提前一点也算高分。"""
    if not peak_hours:
        return 0.3
    dt = datetime.fromtimestamp(now, tz)
    h = dt.hour + dt.minute / 60.0
    best = 0.0
    for p in peak_hours:
        dist = abs(h - p)
        dist = min(dist, 24 - dist)
        fit = math.exp(-(dist * dist) / (2 * 1.5 * 1.5))
        # 峰值前 0.5–2 小时：提前找人比正忙时插话自然
        ahead = (p - h) % 24
        if 0.5 <= ahead <= 2.0:
            fit = max(fit, 0.85)
        best = max(best, fit)
    return round(min(1.0, best), 3)


def silence_fit(hours: float) -> float:
    """12–48 小时最合适；刚聊过为 0；太久衰减。"""
    if hours < 6:
        return 0.0
    if hours < 12:
        return round((hours - 6) / 6 * 0.9, 3)
    if hours <= 48:
        return 1.0
    return round(max(0.2, 1.0 - (hours - 48) / (24 * 7)), 3)


def relation_score(profile: HabitProfile, now: float) -> float:
    """关系热度：主人最高；否则看消息量和认识天数。"""
    if profile.is_owner:
        return 0.9
    by_msg = min(1.0, profile.msg_count / 200.0) * 0.7
    known_days = max(0.0, (now - profile.first_seen) / 86400)
    by_days = min(1.0, known_days / 30.0) * 0.3
    return round(by_msg + by_days, 3)


def load_topics(
    db, account_id: str, qq: str, *, now: float, days: int = 7
) -> dict:
    """找可以接的话头：未说完的事、最近事实、摘要、最近几条对话。"""
    since = now - days * 86400
    facts = db.query(
        "SELECT text FROM facts "
        "WHERE account_id = ? AND subject_type = 'person' AND subject_id = ? "
        "AND gone = 0 AND (valid_until IS NULL OR valid_until > ?) "
        "ORDER BY recorded_at DESC LIMIT 4",
        [account_id, qq, now],
    )
    gists = db.query(
        "SELECT text FROM gists WHERE account_id = ? AND sender_qq = ? AND gone = 0 "
        "ORDER BY period_to DESC LIMIT 2",
        [account_id, qq],
    )
    recent = db.query(
        "SELECT sender_qq, text, event_at FROM episodes "
        "WHERE account_id = ? AND chat_id = ? AND gone = 0 AND event_at >= ? "
        "ORDER BY event_at DESC LIMIT 8",
        [account_id, f"private:{qq}", since],
    )

    unfinished = ""
    markers = ("下次", "回头", "明天", "改天", "再说", "回头聊", "之后", "等我", "后来")
    # 只看最后一条：如果最后是 bot 说的，说明上一句已经接过了；最后是他说的才算没聊完
    last_person = None
    if recent and recent[0]["sender_qq"] == qq:
        last_person = recent[0]
    if last_person is not None:
        text = (last_person["text"] or "").strip()
        is_question = text.endswith(("？", "?", "吗", "呢")) or any(
            w in text for w in ("怎么样", "后来", "结果")
        )
        if text and (is_question or any(w in text for w in markers)):
            unfinished = text

    return {
        "facts": [r["text"] for r in facts if r["text"]],
        "gists": [r["text"] for r in gists if r["text"]],
        "recent": [
            {"self": r["sender_qq"] == "self", "text": r["text"], "at": r["event_at"]}
            for r in recent
            if r["text"]
        ],
        "unfinished": unfinished,
    }


def response_rate(db, account_id: str, qq: str) -> float:
    """以前主动找他，他回不回。Beta 平滑，避免 1/1 就满分。"""
    row = db.query_one(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN replied_at IS NOT NULL OR status = 'replied' THEN 1 ELSE 0 END) AS replied "
        "FROM proactive_log WHERE account_id = ? AND person_qq = ? "
        "AND status IN ('sent', 'replied', 'ignored')",
        [account_id, qq],
    )
    total = int((row["total"] if row else 0) or 0)
    replied = int((row["replied"] if row else 0) or 0)
    return round((replied + 0.35 * 5) / (total + 5), 3)
