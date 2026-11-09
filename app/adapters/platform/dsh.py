"""给外部 agent 平台（DSH / CodeBuddy / TRAE）提供记忆和人格。

接法 B：平台自己管流程，Novabot 只在背后管记忆。

对外三个能力：
  recall   给一段查询，返回拼好的记忆上下文
  remember 把一段对话记进来
  persona  取人格 prompt

外加会话映射：平台的 sessionId ↔ Novabot 的 chat_id
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ...db import Database
from ...models import ChatType, InboundMessage

log = logging.getLogger(__name__)


@dataclass
class SessionMapping:
    """平台会话和 Novabot 会话的对应关系。"""

    platform: str = "dsh"
    external_id: str = ""       # DSH 的 sessionId
    chat_id: str = ""           # Novabot 的 chat_id
    account_id: str = ""
    title: str = ""
    created_at: float = 0.0
    last_active: float = 0.0


class PlatformProvider:
    """给平台用的能力接口。"""

    def __init__(self, db: Database, memory, persona=None):
        self.db = db
        self.memory = memory
        self.persona = persona
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_sessions (
              platform    TEXT NOT NULL,
              external_id TEXT NOT NULL,
              chat_id     TEXT NOT NULL,
              account_id  TEXT NOT NULL DEFAULT '',
              title       TEXT,
              created_at  REAL NOT NULL,
              last_active REAL NOT NULL,
              PRIMARY KEY (platform, external_id)
            )
            """
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ps_chat ON platform_sessions(chat_id)"
        )

    # ==================================================================
    # 会话映射
    # ==================================================================

    def map_session(
        self,
        platform: str,
        external_id: str,
        *,
        account_id: str = "",
        title: str = "",
    ) -> str:
        """把平台会话映射到 Novabot 的 chat_id。已存在就返回原 chat_id。"""
        row = self.db.query_one(
            "SELECT chat_id FROM platform_sessions WHERE platform = ? AND external_id = ?",
            [platform, external_id],
        )
        if row:
            self.db.execute(
                "UPDATE platform_sessions SET last_active = ? WHERE platform = ? AND external_id = ?",
                [time.time(), platform, external_id],
            )
            return row["chat_id"]

        chat_id = f"{platform}:{external_id}"
        now = time.time()
        self.db.execute(
            "INSERT INTO platform_sessions (platform, external_id, chat_id, account_id, title, created_at, last_active) "
            "VALUES (?,?,?,?,?,?,?)",
            [platform, external_id, chat_id, account_id, title, now, now],
        )
        log.info("新会话映射：%s/%s → %s", platform, external_id, chat_id)
        return chat_id

    def bind_session(
        self,
        platform: str,
        external_id: str,
        *,
        chat_id: str,
        account_id: str = "",
        title: str = "",
    ) -> None:
        """把外部平台返回的 session 绑定到已有的 chat_id（不新建 chat_id）。"""
        now = time.time()
        self.db.execute(
            "INSERT INTO platform_sessions "
            "(platform, external_id, chat_id, account_id, title, created_at, last_active) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(platform, external_id) DO UPDATE SET "
            "chat_id=excluded.chat_id, account_id=excluded.account_id, "
            "title=excluded.title, last_active=excluded.last_active",
            [platform, external_id, chat_id, account_id, title, now, now],
        )

    def lookup(self, platform: str, external_id: str) -> str:
        """查 chat_id，没有就建。"""
        return self.map_session(platform, external_id)

    def list_sessions(self, platform: str | None = None, limit: int = 50) -> list[SessionMapping]:
        if platform:
            rows = self.db.query(
                "SELECT * FROM platform_sessions WHERE platform = ? "
                "ORDER BY last_active DESC LIMIT ?",
                [platform, limit],
            )
        else:
            rows = self.db.query(
                "SELECT * FROM platform_sessions ORDER BY last_active DESC LIMIT ?",
                [limit],
            )
        return [
            SessionMapping(
                platform=r["platform"],
                external_id=r["external_id"],
                chat_id=r["chat_id"],
                account_id=r["account_id"],
                title=r["title"] or "",
                created_at=r["created_at"],
                last_active=r["last_active"],
            )
            for r in rows
        ]

    # ==================================================================
    # 记忆能力
    # ==================================================================

    def recall(
        self,
        *,
        platform: str,
        external_id: str,
        sender_qq: str,
        text: str = "",
        account_id: str = "",
        max_chars: int = 300,
    ) -> str:
        """给平台一段记忆上下文。"""
        chat_id = self.lookup(platform, external_id)
        if self.memory is None:
            return ""
        try:
            ctx = self.memory.build_context(
                chat_id=chat_id,
                sender_qq=sender_qq,
                account_id=account_id,
                text=text,
                max_chars=max_chars,
            )
            if hasattr(ctx, "text"):
                return ctx.text
            return str(ctx or "")
        except Exception as e:
            log.warning("platform recall 失败：%s", e)
            return ""

    def remember(
        self,
        *,
        platform: str,
        external_id: str,
        sender_qq: str,
        text: str,
        sender_nick: str = "",
        account_id: str = "",
        is_self: bool = False,
    ) -> int | None:
        """把一段对话记进来。返回 episode id。"""
        if self.memory is None or not text:
            return None
        chat_id = self.lookup(platform, external_id)
        try:
            msg = InboundMessage(
                platform=platform,
                account_id=account_id,
                chat_id=chat_id,
                chat_type=ChatType.PRIVATE,
                sender_qq="self" if is_self else sender_qq,
                sender_nick=sender_nick or sender_qq,
                text=text,
                event_at=time.time(),
                recv_at=time.time(),
            )
            return self.memory.remember(msg)
        except Exception as e:
            log.warning("platform remember 失败：%s", e)
            return None

    def persona_prompt(
        self, *, persona_id: str = "", chat_id: str = "", sender_qq: str = "", text: str = ""
    ) -> str:
        """取人格 prompt。"""
        if self.persona is None:
            return ""
        try:
            return self.persona.build_system_prompt(
                persona_id=persona_id or None,
                chat_id=chat_id,
                sender_qq=sender_qq,
                text=text,
            )
        except Exception as e:
            log.warning("platform persona 失败：%s", e)
            return ""

    # ==================================================================
    # 同步
    # ==================================================================

    def import_history(
        self,
        *,
        platform: str,
        external_id: str,
        messages: list[dict],
        account_id: str = "",
    ) -> int:
        """把平台已有的历史一次性导入。

        messages: [{"role": "user"/"assistant", "name": "...", "content": "..."}]
        """
        # 先确保会话映射存在（一条消息都没导时，会话也要能被列出来）
        self.lookup(platform, external_id)
        n = 0
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content") or ""
            if not content:
                continue
            is_self = role == "assistant"
            self.remember(
                platform=platform,
                external_id=external_id,
                sender_qq=m.get("qq") or ("self" if is_self else m.get("name") or "unknown"),
                sender_nick=m.get("name") or "",
                text=content,
                account_id=account_id,
                is_self=is_self,
            )
            n += 1
        log.info("从 %s/%s 导入 %d 条历史", platform, external_id, n)
        return n
