"""生活模块：模拟作息、当前活动和聊天上下文。

不是真实世界，只是让 bot 在一天里有稳定的“在做什么”。
每天的计划只生成一次，存进 life_days；同一阶段的活动不会每个 tick 变来变去。
"""

from __future__ import annotations

import contextlib
import json
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

WEEKDAY_PHASES = (
    ("sleep", "00:00", "07:30", ("睡觉",), 0.15, "困", False),
    ("wake", "07:30", "08:30", ("刚起床，洗漱完", "在吃早饭", "一边吃早饭一边看消息"), 0.45, "还没完全醒", True),
    ("morning", "08:30", "11:30", ("在整理今天要做的事", "在看书，顺手记点东西", "在听歌，慢慢进入状态"), 0.7, "平静", True),
    ("lunch", "11:30", "13:30", ("在做饭", "在吃午饭", "吃完躺一会儿"), 0.65, "放松", True),
    ("afternoon", "13:30", "17:30", ("在写东西", "在整理歌单", "在处理自己的事", "在窗边发呆"), 0.6, "专注", True),
    ("evening", "17:30", "19:00", ("在做饭", "在吃晚饭", "出去走了一圈"), 0.8, "轻松", True),
    ("leisure", "19:00", "21:30", ("在听歌", "在打游戏", "在看视频", "在翻书"), 0.75, "开心", True),
    ("winddown", "21:30", "23:30", ("洗完澡，在收拾", "在听歌，准备睡了", "趴在床上看手机"), 0.4, "有点困", True),
    ("sleep", "23:30", "24:00", ("准备睡了",), 0.15, "困", False),
)

WEEKEND_PHASES = (
    ("sleep", "00:00", "08:30", ("睡觉",), 0.15, "困", False),
    ("wake", "08:30", "09:30", ("刚起床，头发还乱着", "在吃早午饭", "慢慢醒过来"), 0.45, "懒", True),
    ("morning", "09:30", "12:00", ("在收拾房间", "在听歌", "在阳台晒太阳", "在翻书"), 0.7, "轻松", True),
    ("lunch", "12:00", "14:00", ("在做饭", "在吃午饭", "吃完窝着"), 0.65, "放松", True),
    ("afternoon", "14:00", "18:00", ("出门走走", "在家打游戏", "在看视频", "在写点东西"), 0.7, "开心", True),
    ("evening", "18:00", "19:30", ("在做饭", "在吃晚饭", "在楼下散步"), 0.8, "轻松", True),
    ("leisure", "19:30", "22:30", ("在听歌", "在打游戏", "在看电影", "在翻书"), 0.75, "开心", True),
    ("winddown", "22:30", "23:30", ("洗完澡，准备睡了", "在听歌", "趴在床上看手机"), 0.4, "有点困", True),
    ("sleep", "23:30", "24:00", ("准备睡了",), 0.15, "困", False),
)


WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


@dataclass(slots=True)
class LifeNow:
    account_id: str
    day: str
    weekend: bool
    phase: str
    start: str
    end: str
    activity: str
    energy: float
    mood: str
    can_chat: bool
    next_at: float | None
    next_phase: str
    next_activity: str

    def as_dict(self) -> dict:
        return {
            "day": self.day,
            "weekend": self.weekend,
            "phase": self.phase,
            "start": self.start,
            "end": self.end,
            "activity": self.activity,
            "energy": self.energy,
            "mood": self.mood,
            "can_chat": self.can_chat,
            "next_at": self.next_at,
            "next_phase": self.next_phase,
            "next_activity": self.next_activity,
        }


class LifeService:
    def __init__(self, db, settings=None):
        self.db = db
        self.settings = settings
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        with self.db.tx():
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS life_days ("
                " account_id TEXT NOT NULL, day TEXT NOT NULL,"
                " plan_json TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',"
                " created_at REAL NOT NULL, updated_at REAL NOT NULL,"
                " PRIMARY KEY(account_id, day))"
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS life_events ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " account_id TEXT NOT NULL, day TEXT NOT NULL, at REAL NOT NULL,"
                " phase TEXT NOT NULL, activity TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'phase',"
                " created_at REAL NOT NULL,"
                " UNIQUE(account_id, day, phase))"
            )
            self.db.execute(
                "CREATE INDEX IF NOT EXISTS idx_life_events_day "
                "ON life_events(account_id, day, at)"
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS life_schedules ("
                " account_id TEXT NOT NULL, weekday INTEGER NOT NULL,"
                " slots_json TEXT NOT NULL, updated_at REAL NOT NULL,"
                " PRIMARY KEY(account_id, weekday))"
            )

    def _tz(self):
        name = "Asia/Shanghai"
        try:
            if self.settings is not None:
                name = str(self.settings.get("server.timezone") or name)
        except Exception:  # noqa: BLE001, SIM105
            pass
        try:
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001
            return timezone(timedelta(hours=8), "Asia/Shanghai")

    @staticmethod
    def _day_key(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%d")

    def _default_slots(self, weekday: int) -> list[dict]:
        templates = WEEKEND_PHASES if weekday >= 5 else WEEKDAY_PHASES
        out = []
        for key, start, end, activities, energy, mood, can_chat in templates:
            out.append({
                "key": key,
                "start": start,
                "end": end,
                "activity": activities[0],
                "energy": float(energy),
                "mood": mood,
                "can_chat": bool(can_chat),
            })
        return out

    def _custom_slots(self, account_id: str, weekday: int) -> list[dict] | None:
        row = self.db.query_one(
            "SELECT slots_json FROM life_schedules WHERE account_id = ? AND weekday = ?",
            [account_id, int(weekday)],
        )
        if row is None:
            return None
        try:
            slots = json.loads(row["slots_json"])
        except Exception:  # noqa: BLE001
            return None
        return slots if isinstance(slots, list) and slots else None

    @staticmethod
    def _validate_slots(slots: list) -> list[dict]:
        if not isinstance(slots, list) or not slots:
            raise ValueError("至少要有一个时段")
        clean: list[dict] = []
        for idx, raw in enumerate(slots):
            if not isinstance(raw, dict):
                raise ValueError(f"第 {idx + 1} 个时段格式不对")
            start = str(raw.get("start") or "").strip()
            end = str(raw.get("end") or "").strip()
            if not re.match(r"^\d{1,2}:\d{2}$", start) or not re.match(r"^\d{1,2}:\d{2}$", end):
                raise ValueError(f"第 {idx + 1} 个时段时间格式应为 HH:MM")
            s, e = _minutes(start), _minutes(end)
            if e <= s or e > 24 * 60:
                raise ValueError(f"第 {idx + 1} 个时段结束时间必须晚于开始时间")
            activity = str(raw.get("activity") or "").strip()[:80] or "自由时间"
            mood = str(raw.get("mood") or "").strip()[:20] or "平静"
            try:
                energy = max(0.0, min(1.0, float(raw.get("energy", 0.6))))
            except (TypeError, ValueError):
                energy = 0.6
            clean.append({
                "key": str(raw.get("key") or f"p{idx}")[:30],
                "start": f"{s // 60:02d}:{s % 60:02d}",
                "end": "24:00" if e == 24 * 60 else f"{e // 60:02d}:{e % 60:02d}",
                "activity": activity,
                "energy": round(energy, 2),
                "mood": mood,
                "can_chat": bool(raw.get("can_chat", True)),
            })
        clean.sort(key=lambda x: _minutes(x["start"]))
        if _minutes(clean[0]["start"]) != 0:
            raise ValueError("第一个时段必须从 00:00 开始")
        if _minutes(clean[-1]["end"]) != 24 * 60:
            raise ValueError("最后一个时段必须到 24:00")
        for prev, cur in zip(clean, clean[1:], strict=False):
            if _minutes(prev["end"]) != _minutes(cur["start"]):
                raise ValueError("时段必须首尾相接，不能有空档或重叠")
        return clean

    def schedule(self, account_id: str) -> dict:
        tz = self._tz()
        today = datetime.now(tz).weekday()
        days = []
        for weekday in range(7):
            custom = self._custom_slots(account_id, weekday)
            slots = custom if custom is not None else self._default_slots(weekday)
            days.append({
                "weekday": weekday,
                "name": WEEKDAY_NAMES[weekday],
                "custom": custom is not None,
                "slots": slots,
            })
        return {"ok": True, "account_id": account_id, "today": today, "days": days}

    def _invalidate_today(self, account_id: str) -> None:
        today = self._day_key(datetime.now(self._tz()))
        self.db.execute(
            "DELETE FROM life_days WHERE account_id = ? AND day = ?",
            [account_id, today],
        )

    def save_schedule(self, account_id: str, weekday: int, slots: list) -> dict:
        weekday = int(weekday)
        if weekday < 0 or weekday > 6:
            raise ValueError("weekday 必须是 0–6")
        clean = self._validate_slots(slots)
        now = time.time()
        self.db.execute(
            "INSERT OR REPLACE INTO life_schedules (account_id, weekday, slots_json, updated_at) VALUES (?,?,?,?)",
            [account_id, weekday, json.dumps(clean, ensure_ascii=False), now],
        )
        self._invalidate_today(account_id)
        return {"ok": True, "weekday": weekday, "slots": clean}

    def reset_schedule(self, account_id: str, weekday: int) -> dict:
        weekday = int(weekday)
        if weekday < 0 or weekday > 6:
            raise ValueError("weekday 必须是 0–6")
        self.db.execute(
            "DELETE FROM life_schedules WHERE account_id = ? AND weekday = ?",
            [account_id, weekday],
        )
        self._invalidate_today(account_id)
        return {"ok": True, "weekday": weekday, "slots": self._default_slots(weekday)}

    def _build_plan(self, account_id: str, dt: datetime) -> dict:
        weekend = dt.weekday() >= 5
        custom = self._custom_slots(account_id, dt.weekday())
        if custom:
            return {"weekend": weekend, "phases": [dict(x) for x in custom], "summary": ""}
        templates = WEEKEND_PHASES if weekend else WEEKDAY_PHASES
        day = self._day_key(dt)
        phases = []
        for key, start, end, activities, base_energy, mood, can_chat in templates:
            rng = random.Random(f"{account_id}:{day}:{key}")
            activity = activities[rng.randrange(len(activities))]
            jitter = rng.uniform(-0.06, 0.06)
            energy = round(max(0.05, min(1.0, base_energy + jitter)), 2)
            phases.append({
                "key": key,
                "start": start,
                "end": end,
                "activity": activity,
                "energy": energy,
                "mood": mood,
                "can_chat": can_chat,
            })
        return {"weekend": weekend, "phases": phases, "summary": ""}

    def _ensure_day(self, account_id: str, dt: datetime) -> dict:
        day = self._day_key(dt)
        row = self.db.query_one(
            "SELECT plan_json FROM life_days WHERE account_id = ? AND day = ?",
            [account_id, day],
        )
        if row:
            try:
                return json.loads(row["plan_json"])
            except Exception:  # noqa: BLE001
                pass
        plan = self._build_plan(account_id, dt)
        now = time.time()
        self.db.execute(
            "INSERT OR REPLACE INTO life_days "
            "(account_id, day, plan_json, summary, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            [account_id, day, json.dumps(plan, ensure_ascii=False), "", now, now],
        )
        return plan

    def _phase_for(self, plan: dict, dt: datetime) -> tuple[dict, dict | None]:
        minute = dt.hour * 60 + dt.minute
        phases = plan.get("phases") or []
        current = None
        for phase in phases:
            if _minutes(phase["start"]) <= minute < _minutes(phase["end"]):
                current = phase
                break
        if current is None:
            current = phases[-1]
        idx = phases.index(current)
        nxt = phases[idx + 1] if idx + 1 < len(phases) else None
        return current, nxt

    def current(self, account_id: str, now: float | None = None) -> LifeNow:
        now = float(now or time.time())
        tz = self._tz()
        dt = datetime.fromtimestamp(now, tz)
        plan = self._ensure_day(account_id, dt)
        phase, nxt = self._phase_for(plan, dt)
        day = self._day_key(dt)

        next_at = None
        if nxt is not None:
            eh, em = map(int, nxt["start"].split(":"))
            nd = dt.replace(hour=eh, minute=em, second=0, microsecond=0)
            if nd <= dt:
                nd += timedelta(days=1)
            next_at = nd.timestamp()
        next_phase = nxt["key"] if nxt else "sleep"
        next_activity = nxt["activity"] if nxt else "睡觉"

        # 每个阶段只记一次，重复调用不刷事件
        with contextlib.suppress(Exception):
            self.db.execute(
                "INSERT OR IGNORE INTO life_events "
                "(account_id, day, at, phase, activity, kind, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                [account_id, day, now, phase["key"], phase["activity"], "phase", now],
            )

        return LifeNow(
            account_id=account_id,
            day=day,
            weekend=bool(plan.get("weekend")),
            phase=phase["key"],
            start=phase["start"],
            end=phase["end"],
            activity=phase["activity"],
            energy=float(phase["energy"]),
            mood=str(phase["mood"]),
            can_chat=bool(phase["can_chat"]),
            next_at=next_at,
            next_phase=next_phase,
            next_activity=next_activity,
        )

    def prompt_context(self, account_id: str, now: float | None = None) -> str:
        life = self.current(account_id, now)
        dt = datetime.fromtimestamp(float(now or time.time()), self._tz())
        week = "一二三四五六日"[dt.weekday()]
        nxt = ""
        if life.next_at:
            nxt_dt = datetime.fromtimestamp(life.next_at, self._tz())
            nxt = f"，下一个节点是 {nxt_dt.strftime('%H:%M')} {life.next_activity}"
        return (
            f"【此刻生活】今天是周{week} {dt.strftime('%H:%M')}，你在{life.activity}。"
            f"精力 {life.energy:.1f}，心情{life.mood}{nxt}。"
        )

    def status(self, account_id: str, now: float | None = None) -> dict:
        now = float(now or time.time())
        life = self.current(account_id, now)
        row = self.db.query_one(
            "SELECT plan_json, summary FROM life_days WHERE account_id = ? AND day = ?",
            [account_id, life.day],
        )
        plan = {}
        summary = ""
        if row:
            try:
                plan = json.loads(row["plan_json"])
            except Exception:  # noqa: BLE001
                plan = {}
            summary = row["summary"] or ""
        events = self.db.query(
            "SELECT at, phase, activity FROM life_events "
            "WHERE account_id = ? AND day = ? ORDER BY at",
            [account_id, life.day],
        )
        return {
            "ok": True,
            "account_id": account_id,
            "day": life.day,
            "weekend": life.weekend,
            "now": life.as_dict(),
            "next": {
                "at": life.next_at,
                "phase": life.next_phase,
                "activity": life.next_activity,
            },
            "phases": plan.get("phases") or [],
            "summary": summary,
            "events": [dict(r) for r in events],
        }

    def timeline(self, account_id: str, now: float | None = None) -> list[dict]:
        return self.status(account_id, now).get("phases") or []
