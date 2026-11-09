"""提醒服务：到点直接发消息，不经过模型。

每 30 秒看一次，失败重试 3 次；发出去写 sent，失败写 failed。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)


class ReminderService:
    def __init__(self, db, manager, *, clock=time.time, interval: float = 30.0):
        self.db = db
        self.manager = manager
        self.clock = clock
        self.interval = interval
        self._running = False
        self.tz = self._tz()

    def _tz(self):
        try:
            from ..settings import Settings

            name = Settings(self.db).get("server.timezone") or "Asia/Shanghai"
            return ZoneInfo(str(name))
        except Exception:  # noqa: BLE001
            return datetime.now().astimezone().tzinfo

    async def run_forever(self) -> None:
        self._running = True
        log.info("提醒服务已启动")
        while self._running:
            await asyncio.sleep(self.interval)
            try:
                await self.check_due()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("检查提醒出错")

    def stop(self) -> None:
        self._running = False

    async def check_due(self, now: float | None = None) -> list[dict]:
        now = float(now or self.clock())
        rows = self.db.query(
            "SELECT * FROM reminders WHERE status = 'pending' AND due_at <= ? "
            "ORDER BY due_at LIMIT 20",
            [now],
        )
        out: list[dict] = []
        for row in rows:
            text = f"⏰ 提醒：{row['text']}"
            ok = False
            try:
                ok = await self.manager.send_to_chat(
                    row["account_id"], row["chat_id"], text
                )
            except Exception as e:  # noqa: BLE001
                log.warning("提醒发送异常（%s）：%s", row["id"], e)

            if ok:
                self.db.execute(
                    "UPDATE reminders SET status='sent', sent_at=?, attempts=attempts+1 "
                    "WHERE id=?",
                    [now, row["id"]],
                )
                out.append({"id": row["id"], "status": "sent", "text": row["text"]})
            else:
                attempts = int(row["attempts"] or 0) + 1
                status = "failed" if attempts >= 3 else "pending"
                self.db.execute(
                    "UPDATE reminders SET attempts=?, status=?, error=? WHERE id=?",
                    [attempts, status, "发送失败", row["id"]],
                )
                out.append(
                    {
                        "id": row["id"],
                        "status": status,
                        "text": row["text"],
                        "attempts": attempts,
                    }
                )
        return out
