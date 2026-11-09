"""主动私聊服务。

一个循环：算候选 → 打分 → 生成 → 发送 → 记状态。
用户回复时反向更新状态（回得越快/越勤，下次越容易再找）。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..life_service import LifeService
from ..models import ChatType, InboundMessage
from ..settings import Settings
from .habits import (
    HabitProfile,
    day_bounds,
    is_quiet,
    load_habit,
    load_topics,
    local_day,
    response_rate,
)
from .strategy import Candidate, score_candidate

log = logging.getLogger(__name__)

_SLOT_DEFS = (
    ("morning", 8, 11),
    ("noon", 11, 14),
    ("afternoon", 14, 18),
    ("evening", 18, 23),
)


DEFAULT_SYSTEM = (
    "你是一个在 QQ 上和熟人聊天的人，说话自然、口语化、简短。"
    "你不是客服，也不是助手。"
)


@dataclass(slots=True)
class _State:
    last_sent_at: float | None = None
    last_reply_at: float | None = None
    ignored_streak: int = 0
    next_eligible_at: float | None = None
    daily_date: str = ""
    daily_count: int = 0


class ProactiveService:
    def __init__(self, db, memory, persona, settings, *, manager, clock=time.time):
        self.db = db
        self.memory = memory
        self.persona = persona
        self.settings = settings
        self.manager = manager
        self.clock = clock
        self._running = False
        self.tz = self._resolve_tz()
        self.life = LifeService(db, settings)
        self._recent: dict[tuple[str, str], list[str]] = {}

    # ==================================================================
    # 基础
    # ==================================================================

    def _resolve_tz(self):
        name = self._setting(self.settings, "server.timezone", "Asia/Shanghai") or "Asia/Shanghai"
        try:
            return ZoneInfo(str(name))
        except Exception:  # noqa: BLE001
            # Windows 没装 tzdata 时兜底：至少上海时区别错
            if str(name) == "Asia/Shanghai":
                return timezone(timedelta(hours=8), "Asia/Shanghai")
            return datetime.now().astimezone().tzinfo

    def _settings_for(self, account_id: str) -> Settings:
        return Settings(self.db, account_id=account_id or "")

    @staticmethod
    def _setting(settings, key: str, default=None):
        try:
            return settings.get(key)
        except Exception:  # noqa: BLE001
            return default

    def enabled(self, account_id: str = "") -> bool:
        """主动搭话是否至少有一个账号开着。

        以前只看全局设置，聊天页按账号打开后 tick 仍被全局开关挡住。
        现在优先看账号自己的设置；没有账号上下文时才回退全局。
        """
        if account_id:
            return bool(self._setting(self._settings_for(account_id), "proactive.enabled", False))
        accounts = self._online_accounts()
        if accounts:
            return any(
                bool(self._setting(self._settings_for(acc), "proactive.enabled", False))
                for acc in accounts
            )
        return bool(self._setting(self.settings, "proactive.enabled", False))

    def _online_accounts(self) -> list[str]:
        getter = getattr(self.manager, "online_accounts", None)
        if callable(getter):
            return list(getter())
        return []

    # ==================================================================
    # 主循环
    # ==================================================================

    async def run_forever(self) -> None:
        self._running = True
        log.info("主动私聊循环已启动")
        while self._running:
            interval = int(self._setting(self.settings, "proactive.tick_sec", 300) or 300)
            # 加点抖动，别每次都在整点醒
            await asyncio.sleep(interval + random.uniform(0, 20))
            try:
                if not self.enabled():
                    continue
                decisions = await self.tick()
                for d in decisions:
                    if d.get("status") in ("sent", "failed"):
                        log.info("主动私聊：%s", d)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("主动私聊 tick 出错")

    def stop(self) -> None:
        self._running = False

    # ==================================================================
    # 一次 tick
    # ==================================================================

    async def tick(
        self, *, now: float | None = None, account_id: str = "", dry_run: bool = False
    ) -> list[dict]:
        now = float(now or self.clock())
        if not self.enabled(account_id):
            return [{"status": "skipped", "reason": "主动搭话关着"}]

        accounts = [account_id] if account_id else self._online_accounts()
        if not accounts:
            return [{"status": "skipped", "reason": "没有在线账号"}]
        self._mark_ignored(now)
        decisions: list[dict] = []
        global_sent = self._today_sent(now)

        for acc in accounts:
            settings = self._settings_for(acc)
            if not self._setting(settings, "proactive.enabled", False):
                continue

            max_global = int(self._setting(settings, "proactive.global_max_per_day", 10) or 0)
            if max_global and global_sent >= max_global:
                decisions.append(
                    {"account_id": acc, "status": "skipped", "reason": "全局今日上限"}
                )
                continue

            quiet = is_quiet(
                now,
                int(self._setting(settings, "proactive.quiet_start", 23)),
                int(self._setting(settings, "proactive.quiet_end", 8)),
                self.tz,
            )
            if quiet:
                decisions.append(
                    {"account_id": acc, "status": "skipped", "reason": "安静时段"}
                )
                continue

            candidates = self._collect(acc, settings, now)
            if not candidates:
                decisions.append(
                    {"account_id": acc, "status": "skipped", "reason": "没有满足条件的候选人"}
                )
                continue

            cand = self._pick(candidates)
            threshold = float(self._setting(settings, "proactive.score_threshold", 0.5) or 0)
            if cand.score < threshold:
                decisions.append(
                    {
                        "account_id": acc,
                        "qq": cand.qq,
                        "name": cand.name,
                        "score": cand.score,
                        "status": "skipped",
                        "reason": f"分数 {cand.score:.2f} 低于门槛 {threshold:.2f}",
                    }
                )
                continue

            try:
                text = await self._generate(acc, cand, now, settings)
            except Exception as e:  # noqa: BLE001
                decisions.append(
                    {
                        "account_id": acc,
                        "qq": cand.qq,
                        "name": cand.name,
                        "score": cand.score,
                        "status": "failed",
                        "reason": f"生成失败：{e}",
                    }
                )
                continue

            decision = {
                "account_id": acc,
                "qq": cand.qq,
                "name": cand.name,
                "score": cand.score,
                "reason": cand.reason(),
                "message": text,
                "peak_hours": cand.peak_hours,
                "silence_hours": cand.silence_hours,
            }

            global_master = bool(self._setting(self.settings, "runtime.master_enabled", True))
            global_dry = bool(self._setting(self.settings, "runtime.dry_run", False))
            if not global_master:
                decision["status"] = "skipped"
                decision["reason"] = "全局总开关关着"
                decisions.append(decision)
                continue
            if dry_run or global_dry or self._setting(settings, "proactive.dry_run", False):
                decision["status"] = "preview"
                decisions.append(decision)
                continue

            try:
                ok = await self.manager.send_private(acc, cand.qq, text)
            except Exception as e:  # noqa: BLE001
                ok = False
                text_error = str(e)
            else:
                text_error = ""
            if not ok:
                self._log_failed(acc, cand, text, now, text_error or "发送失败")
                decision["status"] = "failed"
                decision["reason"] = "发送失败"
                decisions.append(decision)
                continue

            self._save_sent(acc, cand, text, now)
            global_sent += 1
            decision["status"] = "sent"
            decisions.append(decision)

        return decisions

    # ==================================================================
    # 候选
    # ==================================================================

    def _slot_for(self, now: float) -> tuple[str, int, int] | None:
        hour = datetime.fromtimestamp(now, self.tz).hour
        for key, start, end in _SLOT_DEFS:
            if start <= hour < end:
                return key, start, end
        return None

    def _slot_sent(self, account_id: str, qq: str, now: float, start: int, end: int) -> int:
        dt = datetime.fromtimestamp(now, self.tz)
        start_dt = dt.replace(hour=start, minute=0, second=0, microsecond=0)
        end_dt = dt.replace(hour=end, minute=0, second=0, microsecond=0)
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM proactive_log "
            "WHERE account_id = ? AND person_qq = ? AND sent_at >= ? AND sent_at < ? "
            "AND status <> 'failed'",
            [account_id, qq, start_dt.timestamp(), end_dt.timestamp()],
        )
        return int((row["n"] if row else 0) or 0)

    def _slot_quota(self, settings, key: str) -> int:
        if key == "morning":
            return int(self._setting(settings, "proactive.slot_morning", 1) or 0)
        if key == "noon":
            return int(self._setting(settings, "proactive.slot_noon", 1) or 0)
        if key == "afternoon":
            return int(self._setting(settings, "proactive.slot_afternoon", 1) or 0)
        if key == "evening":
            return int(self._setting(settings, "proactive.slot_evening", 1) or 0)
        return 0

    def _collect(self, account_id: str, settings, now: float) -> list[Candidate]:
        min_messages = int(self._setting(settings, "proactive.min_messages", 30) or 1)
        min_days = float(self._setting(settings, "proactive.min_days", 2) or 0)
        habit_days = int(self._setting(settings, "proactive.habit_days", 30) or 30)
        min_silence = float(self._setting(settings, "proactive.min_silence_hours", 3) or 0)
        guard_min = int(self._setting(settings, "proactive.active_guard_min", 10) or 0)
        only_owner = bool(self._setting(settings, "proactive.only_owner", False))
        owner_enabled = bool(self._setting(settings, "proactive.owner_enabled", True))
        private_access = str(settings.get("chat.private_access") or "all")
        if private_access == "off":
            return []
        raw_allow = settings.get("security.private_whitelist") or []
        if isinstance(raw_allow, str):
            allow = {x.strip() for x in raw_allow.replace("，", ",").split(",") if x.strip()}
        else:
            allow = {str(x).strip() for x in raw_allow if str(x).strip()}
        max_per_day = int(self._setting(settings, "proactive.max_per_day", 6) or 0)
        owner_max = int(self._setting(settings, "proactive.owner_max_per_day", 6) or 0)
        backoff_step = max(1, int(self._setting(settings, "proactive.backoff_base_days", 2) or 2))
        today = local_day(now, self.tz)
        slot = self._slot_for(now)
        if slot is None:
            return []
        slot_key, slot_start, slot_end = slot
        slot_quota = self._slot_quota(settings, slot_key)
        if slot_quota <= 0:
            return []

        life = self.life.current(account_id, now)
        if not life.can_chat:
            return []

        rows = self.db.query(
            "SELECT p.qq FROM people p WHERE p.account_id = ? AND p.gone = 0 "
            "AND p.qq <> 'self' AND p.msg_count >= ? "
            "AND EXISTS (SELECT 1 FROM episodes e WHERE e.account_id = p.account_id "
            "AND e.sender_qq = p.qq AND e.chat_id = 'private:' || p.qq AND e.gone = 0)",
            [account_id, max(1, min_messages)],
        )

        out: list[Candidate] = []
        for row in rows:
            qq = row["qq"]
            profile = load_habit(
                self.db,
                account_id,
                qq,
                now=now,
                tz=self.tz,
                days=habit_days,
                min_messages=min_messages,
            )
            if profile is None:
                continue
            if (now - profile.first_seen) / 86400 < min_days:
                continue
            if profile.is_owner and not owner_enabled:
                continue
            if only_owner and not profile.is_owner:
                continue
            if private_access == "owner" and not profile.is_owner:
                continue
            if private_access == "whitelist" and not profile.is_owner and qq not in allow:
                continue

            silence = (now - profile.last_seen) / 3600
            if silence < min_silence:
                continue
            if (now - profile.last_seen) < guard_min * 60:
                continue

            state = self._load_state(account_id, qq)
            streak = 0
            if state is not None:
                daily = state.daily_count if state.daily_date == today else 0
                limit = owner_max if profile.is_owner else max_per_day
                if state.ignored_streak:
                    limit = max(1, limit - backoff_step * min(state.ignored_streak, 2))
                if limit and daily >= limit:
                    continue
                if state.next_eligible_at and state.next_eligible_at > now:
                    continue
                streak = state.ignored_streak

            if self._slot_sent(account_id, qq, now, slot_start, slot_end) >= slot_quota:
                continue

            topics = load_topics(self.db, account_id, qq, now=now)
            resp = response_rate(self.db, account_id, qq)
            out.append(
                score_candidate(
                    profile,
                    topics,
                    resp,
                    now=now,
                    tz=self.tz,
                    ignored_streak=streak,
                )
            )

        out.sort(key=lambda c: c.score, reverse=True)
        return out

    @staticmethod
    def _pick(candidates: list[Candidate]) -> Candidate:
        top = candidates[:3]
        if len(top) == 1:
            return top[0]
        # 分数接近的加一点抖动，避免每天都是同一个人
        return max(top, key=lambda c: c.score + random.uniform(-0.03, 0.03))

    # ==================================================================
    # 生成
    # ==================================================================

    async def _generate(self, account_id: str, cand: Candidate, now: float, settings) -> str:
        llm = getattr(self.manager, "llm_for", lambda _a: None)(account_id)
        if llm is None:
            raise RuntimeError("没有可用模型")

        system = self._system_prompt(account_id, cand.qq)
        prompt = self._prompt(account_id, cand, now, settings)
        reply = await llm.chat(
            system=system,
            messages=[{"role": "user", "content": prompt}],
            session_id=f"private:{cand.qq}",
            account_id=account_id,
            max_tokens=160,
            temperature=0.9,
        )
        if getattr(reply, "error", None):
            raise RuntimeError(reply.error)
        return self._clean_message(str(getattr(reply, "text", "") or ""), account_id, cand.qq, settings)

    def _system_prompt(self, account_id: str, qq: str) -> str:
        if self.persona is not None:
            try:
                text = self.persona.build_system_prompt(
                    chat_id=f"private:{qq}", sender_qq=qq, text="", account_id=account_id
                )
                if text:
                    return text
            except Exception:  # noqa: BLE001
                pass
        return DEFAULT_SYSTEM

    def _prompt(self, account_id: str, cand: Candidate, now: float, settings) -> str:
        dt = datetime.fromtimestamp(now, self.tz)
        week = "一二三四五六日"[dt.weekday()]
        t = cand.topics or {}
        lines = [
            f"现在是 {dt.strftime('%Y-%m-%d %H:%M')} 周{week}。",
            self.life.prompt_context(account_id, now),
            f"你要主动给「{cand.name}」发一条 QQ 私聊。",
            f"他发过 {cand.msg_count} 条消息，上次说话是 {cand.silence_hours:.1f} 小时前。",
        ]

        recent = list(reversed(t.get("recent") or []))
        if recent:
            lines.append("最近聊到：")
            for r in recent[-6:]:
                who = "你" if r.get("self") else "他"
                lines.append(f"- {who}：{str(r.get('text'))[:80]}")

        if t.get("facts"):
            lines.append("你记得：")
            lines.extend(f"- {x}" for x in t["facts"][:4])
        if t.get("unfinished"):
            lines.append(f"没聊完的事：{t['unfinished'][:80]}")

        last = self.db.query(
            "SELECT message FROM proactive_log WHERE account_id = ? AND person_qq = ? "
            "ORDER BY sent_at DESC LIMIT 3",
            [account_id, cand.qq],
        )
        if last:
            lines.append("你最近主动说过：")
            lines.extend(f"- {r['message']}" for r in last)

        max_chars = int(self._setting(settings, "proactive.max_chars", 60) or 60)
        lines.extend(
            [
                "要求：",
                f"1. 只写一条消息，不超过 {max_chars} 字",
                "2. 优先接上面没聊完的事；没有就写一句此刻自然的话",
                '3. 不要以“在吗/在么/你好”开头，不要客服腔，不要解释你是 AI',
                "4. 只输出消息本身，不要引号、不要解释",
            ]
        )
        return "\n".join(lines)

    def _clean_message(self, text: str, account_id: str, qq: str, settings) -> str:
        text = (text or "").strip().strip('"“”').strip()
        text = text.splitlines()[0].strip() if text else ""
        for prefix in ("消息：", "消息:", "回复：", "回复:"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
        if not text:
            raise RuntimeError("模型没生成内容")
        if text.startswith(("在吗", "在么", "在不在")):
            raise RuntimeError("开场太像试探/客服")

        max_chars = int(self._setting(settings, "proactive.max_chars", 60) or 60)
        if len(text) > max_chars:
            keep = max(1, max_chars - 1)
            text = text[:keep].rstrip("，,。.、 　") + "…"

        prev = self.db.query(
            "SELECT message FROM proactive_log WHERE account_id = ? AND person_qq = ? "
            "ORDER BY sent_at DESC LIMIT 3",
            [account_id, qq],
        )
        if any(_norm(r["message"]) == _norm(text) for r in prev):
            raise RuntimeError("和最近一条主动消息重复")
        return text

    # ==================================================================
    # 状态与日志
    # ==================================================================

    def _load_state(self, account_id: str, qq: str) -> _State | None:
        row = self.db.query_one(
            "SELECT * FROM proactive_state WHERE account_id = ? AND person_qq = ?",
            [account_id, qq],
        )
        if row is None:
            return None
        return _State(
            last_sent_at=row["last_sent_at"],
            last_reply_at=row["last_reply_at"],
            ignored_streak=int(row["ignored_streak"] or 0),
            next_eligible_at=row["next_eligible_at"],
            daily_date=row["daily_date"] or "",
            daily_count=int(row["daily_count"] or 0),
        )

    def _save_sent(self, account_id: str, cand: Candidate, text: str, now: float) -> None:
        today = local_day(now, self.tz)
        self.db.execute(
            "INSERT INTO proactive_log "
            "(account_id, person_qq, person_name, sent_at, message, topic, score, reason, "
            " status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                account_id,
                cand.qq,
                cand.name,
                now,
                text,
                (cand.topics or {}).get("unfinished") or "",
                cand.score,
                cand.reason(),
                "sent",
                now,
            ],
        )
        state = self._load_state(account_id, cand.qq)
        daily = (state.daily_count if state and state.daily_date == today else 0) + 1
        self._upsert_state(
            account_id,
            cand.qq,
            now,
            daily_date=today,
            daily_count=daily,
            last_sent_at=now,
            last_status="sent",
            last_score=cand.score,
        )
        if self.memory is not None:
            try:
                self.memory.remember(
                    InboundMessage(
                        platform="qq",
                        account_id=account_id,
                        chat_id=f"private:{cand.qq}",
                        chat_type=ChatType.PRIVATE,
                        sender_qq="self",
                        sender_nick="self",
                        text=text,
                        event_at=now,
                        recv_at=now,
                    )
                )
            except Exception as e:  # noqa: BLE001
                log.warning("写主动消息到记忆失败：%s", e)

    def _log_failed(self, account_id: str, cand: Candidate, text: str, now: float, error: str) -> None:
        self.db.execute(
            "INSERT INTO proactive_log "
            "(account_id, person_qq, person_name, sent_at, message, topic, score, reason, "
            " status, error, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                account_id,
                cand.qq,
                cand.name,
                now,
                text,
                (cand.topics or {}).get("unfinished") or "",
                cand.score,
                cand.reason(),
                "failed",
                error[:200],
                now,
            ],
        )

    def _upsert_state(self, account_id: str, qq: str, now: float, **fields) -> None:
        state = self._load_state(account_id, qq) or _State()
        values = {
            "last_sent_at": state.last_sent_at,
            "last_reply_at": state.last_reply_at,
            "ignored_streak": state.ignored_streak,
            "next_eligible_at": state.next_eligible_at,
            "daily_date": state.daily_date,
            "daily_count": state.daily_count,
            "last_score": None,
            "last_status": "",
        }
        values.update(fields)
        self.db.execute(
            "INSERT INTO proactive_state "
            "(account_id, person_qq, last_sent_at, last_reply_at, ignored_streak, "
            " next_eligible_at, daily_date, daily_count, last_score, last_status, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(account_id, person_qq) DO UPDATE SET "
            " last_sent_at=excluded.last_sent_at, last_reply_at=excluded.last_reply_at, "
            " ignored_streak=excluded.ignored_streak, next_eligible_at=excluded.next_eligible_at, "
            " daily_date=excluded.daily_date, daily_count=excluded.daily_count, "
            " last_score=excluded.last_score, last_status=excluded.last_status, "
            " updated_at=excluded.updated_at",
            [
                account_id,
                qq,
                values["last_sent_at"],
                values["last_reply_at"],
                values["ignored_streak"],
                values["next_eligible_at"],
                values["daily_date"],
                values["daily_count"],
                values["last_score"],
                values["last_status"],
                now,
            ],
        )

    def _mark_ignored(self, now: float) -> None:
        rows = self.db.query(
            "SELECT id, account_id, person_qq, sent_at FROM proactive_log "
            "WHERE status = 'sent' AND replied_at IS NULL"
        )
        for row in rows:
            settings = self._settings_for(row["account_id"])
            wait = float(self._setting(settings, "proactive.wait_reply_hours", 24) or 24) * 3600
            if now - float(row["sent_at"]) < wait:
                continue
            self.db.execute(
                "UPDATE proactive_log SET status = 'ignored' WHERE id = ?", [row["id"]]
            )
            state = self._load_state(row["account_id"], row["person_qq"]) or _State()
            streak = state.ignored_streak + 1
            self._upsert_state(
                row["account_id"],
                row["person_qq"],
                now,
                ignored_streak=streak,
                next_eligible_at=None,
                last_status="ignored",
            )
            log.info(
                "主动消息被无视：%s/%s 连续 %d 次，接下来降频",
                row["account_id"], row["person_qq"], streak,
            )

    def on_user_message(self, account_id: str, qq: str, text: str, at: float | None = None) -> bool:
        """用户回了私聊：把最近一条未回的主动消息标记为 replied。"""
        at = float(at or self.clock())
        row = self.db.query_one(
            "SELECT id, sent_at FROM proactive_log "
            "WHERE account_id = ? AND person_qq = ? AND status IN ('sent', 'ignored') "
            "AND replied_at IS NULL ORDER BY sent_at DESC LIMIT 1",
            [account_id, qq],
        )
        if row is None:
            return False
        if at - float(row["sent_at"]) > 72 * 3600:
            return False

        self.db.execute(
            "UPDATE proactive_log SET replied_at = ?, reply_text = ?, status = 'replied' "
            "WHERE id = ?",
            [at, (text or "")[:500], row["id"]],
        )
        settings = self._settings_for(account_id)
        interval = float(self._setting(settings, "proactive.interval_hours", 2) or 0) * 3600
        self._upsert_state(
            account_id,
            qq,
            at,
            last_reply_at=at,
            ignored_streak=0,
            next_eligible_at=at + interval,
            last_status="replied",
        )
        log.info("主动消息被回复：%s/%s，退避清零", account_id, qq)
        return True

    def _today_sent(self, now: float) -> int:
        start, end = day_bounds(now, self.tz)
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM proactive_log "
            "WHERE sent_at >= ? AND sent_at < ? AND status <> 'failed'",
            [start, end],
        )
        return int((row["n"] if row else 0) or 0)

    # ==================================================================
    # 调试用
    # ==================================================================

    async def preview(self, *, account_id: str = "", now: float | None = None) -> list[dict]:
        """按现在的时间算一遍，会生成内容，但不发送、不写状态。"""
        now = float(now or self.clock())
        accounts = [account_id] if account_id else self._online_accounts()
        if not accounts:
            return [{"status": "skipped", "reason": "没有在线账号"}]

        out: list[dict] = []
        for acc in accounts:
            settings = self._settings_for(acc)
            candidates = self._collect(acc, settings, now)
            if not candidates:
                out.append(
                    {"account_id": acc, "status": "skipped", "reason": "没有满足条件的候选人"}
                )
                continue
            cand = candidates[0]
            try:
                text = await self._generate(acc, cand, now, settings)
            except Exception as e:  # noqa: BLE001
                out.append(
                    {
                        "account_id": acc,
                        "qq": cand.qq,
                        "name": cand.name,
                        "score": cand.score,
                        "status": "failed",
                        "reason": f"生成失败：{e}",
                    }
                )
                continue
            out.append(
                {
                    "account_id": acc,
                    "qq": cand.qq,
                    "name": cand.name,
                    "score": cand.score,
                    "reason": cand.reason(),
                    "message": text,
                    "peak_hours": cand.peak_hours,
                    "silence_hours": cand.silence_hours,
                    "status": "preview",
                }
            )
        return out

    async def fire(
        self, account_id: str, qq: str, text: str = "", *, now: float | None = None
    ) -> dict:
        """手动叫她去说一句。不检查时段和频率，但长度/重复仍然校验。"""
        now = float(now or self.clock())
        settings = self._settings_for(account_id)
        profile = self._profile_for_manual(account_id, qq, now)
        if profile is None:
            return {"status": "failed", "reason": "这个人不在库里"}

        topics = load_topics(self.db, account_id, qq, now=now)
        cand = score_candidate(
            profile, topics, response_rate(self.db, account_id, qq), now=now, tz=self.tz
        )
        try:
            if text:
                message = self._clean_message(text, account_id, qq, settings)
            else:
                message = await self._generate(account_id, cand, now, settings)
        except Exception as e:  # noqa: BLE001
            return {"status": "failed", "reason": str(e)}

        ok = await self.manager.send_private(account_id, qq, message)
        if not ok:
            self._log_failed(account_id, cand, message, now, "发送失败")
            return {"status": "failed", "reason": "发送失败", "message": message}

        self._save_sent(account_id, cand, message, now)
        return {
            "status": "sent",
            "account_id": account_id,
            "qq": qq,
            "name": cand.name,
            "message": message,
            "score": cand.score,
        }

    def _profile_for_manual(self, account_id: str, qq: str, now: float) -> HabitProfile | None:
        person = self.db.query_one(
            "SELECT qq, name, alias, msg_count, first_seen, last_seen, is_owner "
            "FROM people WHERE account_id = ? AND qq = ? AND gone = 0",
            [account_id, qq],
        )
        if person is None:
            return None
        profile = load_habit(
            self.db, account_id, qq, now=now, tz=self.tz, days=30, min_messages=0
        )
        if profile is not None:
            return profile
        return HabitProfile(
            qq=qq,
            name=person["alias"] or person["name"] or qq,
            is_owner=bool(person["is_owner"]),
            msg_count=int(person["msg_count"] or 0),
            total=0,
            active_days=1,
            first_seen=float(person["first_seen"] or now),
            last_seen=float(person["last_seen"] or now),
            hours=[0] * 24,
            peak_hours=[],
            avg_per_day=0.0,
        )

    def status(self, *, now: float | None = None, account_id: str = "") -> dict:
        now = float(now or self.clock())
        accounts = [account_id] if account_id else self._online_accounts()
        start, end = day_bounds(now, self.tz)
        status_settings = self._settings_for(account_id) if account_id else self.settings
        out = {
            "ok": True,
            "enabled": self.enabled(account_id),
            "quiet_now": is_quiet(
                now,
                int(self._setting(status_settings, "proactive.quiet_start", 23)),
                int(self._setting(status_settings, "proactive.quiet_end", 8)),
                self.tz,
            ),
            "today_sent": self._today_sent(now),
            "accounts": [],
            "logs": [],
        }
        for acc in accounts:
            settings = self._settings_for(acc)
            candidates = self._collect(acc, settings, now)
            row = self.db.query_one(
                "SELECT COUNT(*) AS n FROM proactive_log "
                "WHERE account_id = ? AND sent_at >= ? AND sent_at < ? AND status <> 'failed'",
                [acc, start, end],
            )
            out["accounts"].append(
                {
                    "id": acc,
                    "today_sent": int((row["n"] if row else 0) or 0),
                    "candidates": [
                        {
                            "qq": c.qq,
                            "name": c.name,
                            "score": c.score,
                            "reason": c.reason(),
                            "silence_hours": c.silence_hours,
                            "peak_hours": c.peak_hours,
                            "is_owner": c.is_owner,
                        }
                        for c in candidates[:5]
                    ],
                }
            )

        out["logs"] = [
            dict(r)
            for r in self.db.query(
                "SELECT * FROM proactive_log ORDER BY id DESC LIMIT 10"
            )
        ]
        return out


def _norm(text: str) -> str:
    import re

    return re.sub(r"[\s，,。.!！?？~～]", "", text or "")
