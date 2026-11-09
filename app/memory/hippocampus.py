"""海马体：情景记忆。

职责：存原始事件，按时间顺序，最细的粒度。

写入路径上不能有模型调用——判情绪、建边都是异步的。
"""

from __future__ import annotations

import json
import logging
import time

from ..db import Database
from ..models import ChatType, Episode, InboundMessage

log = logging.getLogger(__name__)


class Hippocampus:
    def __init__(self, db: Database, *, retention_days: int = 7):
        self.db = db
        self.retention_days = retention_days
        # FTS5 可能没编译（Linux 上常见），探测一次记住
        try:
            v = db.scalar("SELECT v FROM meta WHERE k = 'fts5_enabled'")
            self.has_fts = v == "1"
        except Exception:
            self.has_fts = False

    # -- 写入 ------------------------------------------------------------

    def write(self, msg: InboundMessage) -> int:
        """写一条情景，返回 episode id。

        顺带确保 chats 和 people 里有对应记录。
        """
        now = time.time()
        chat_type = msg.chat_type.value if isinstance(msg.chat_type, ChatType) else str(msg.chat_type)
        group_id = msg.group_id

        # 私聊的 chat_id 是 private:xxx，群聊是 group:xxx，兜底从 chat_id 解析
        if not group_id and chat_type == "group" and msg.chat_id.startswith("group:"):
            group_id = msg.chat_id[6:]

        self._ensure_chat(msg, chat_type, group_id, now)
        # bot 自己的回复也进 episodes（不然多轮对话是断的），
        # 但别把它当成一个人写进 people
        if msg.sender_qq and msg.sender_qq != "self":
            self._ensure_person(msg, now)

        ep_id = self.db.insert(
            """
            INSERT INTO episodes (
              account_id, platform, chat_id, chat_type, group_id, group_name,
              sender_qq, sender_uin, sender_nick, sender_card, sender_remark, sender_level,
              text, raw, msg_type, reply_to, at_list,
              event_at, recv_at, distilled, gone, gone_reason, meta
            ) VALUES (?,?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?,?)
            """,
            [
                msg.account_id or "default", msg.platform, msg.chat_id, chat_type, group_id, msg.group_name,
                msg.sender_qq, msg.sender_uin, msg.sender_nick, msg.sender_card,
                msg.sender_remark, self._level_value(msg.sender_level),
                msg.text, json.dumps(msg.raw, ensure_ascii=False) if msg.raw else None,
                msg.msg_type.value if hasattr(msg.msg_type, "value") else str(msg.msg_type),
                msg.reply_to,
                json.dumps(msg.at_list, ensure_ascii=False) if msg.at_list else None,
                msg.event_at, msg.recv_at, 0, 0, None, None,
            ],
        )

        # 更新统计
        self.db.execute(
            "UPDATE chats SET msg_count = msg_count + 1, last_seen = ? "
            "WHERE chat_id = ? AND account_id = ?",
            [msg.event_at, msg.chat_id, msg.account_id or "default"],
        )
        if msg.sender_qq and msg.sender_qq != "self":
            self.db.execute(
                "UPDATE people SET msg_count = msg_count + 1, last_seen = ? "
                "WHERE qq = ? AND account_id = ?",
                [msg.event_at, msg.sender_qq, msg.account_id or "default"],
            )

        return ep_id

    def _ensure_account(self, account_id: str, now: float) -> str:
        """chats 和 people 都有外键指向 accounts，先确保它有。

        account_id 为空时用 'default' —— 平台适配和临时测试都可能不传。
        """
        acc = account_id or "default"
        exists = self.db.query_one("SELECT 1 FROM accounts WHERE id = ?", [acc])
        if exists:
            return acc
        self.db.execute(
            "INSERT INTO accounts (id, name, platform, enabled, dry_run, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            [acc, acc, "qq", 0, 0, now, now],
        )
        return acc

    def _ensure_chat(self, msg: InboundMessage, chat_type: str, group_id: str | None, now: float) -> None:
        acc = self._ensure_account(msg.account_id, now)
        row = self.db.query_one(
            "SELECT title, title_history FROM chats WHERE chat_id = ? AND account_id = ?",
            [msg.chat_id, acc],
        )
        title = msg.group_name if chat_type == "group" else msg.sender_nick

        if row is None:
            self.db.execute(
                """
                INSERT INTO chats (
                  chat_id, account_id, platform, chat_type, group_id, title,
                  title_history, msg_count, first_seen, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                [msg.chat_id, acc, msg.platform, chat_type, group_id, title,
                 json.dumps([{"title": title, "from": msg.event_at, "to": None}], ensure_ascii=False) if title else None,
                 0, msg.event_at, msg.event_at],
            )
            return

        # 群名变了记一笔
        old_title = row["title"]
        if title and old_title and title != old_title:
            hist = json.loads(row["title_history"] or "[]")
            for h in hist:
                if h.get("to") is None:
                    h["to"] = msg.event_at
                    break
            hist.append({"title": title, "from": msg.event_at, "to": None})
            self.db.execute(
                "UPDATE chats SET title = ?, title_history = ? WHERE chat_id = ? AND account_id = ?",
                [title, json.dumps(hist, ensure_ascii=False), msg.chat_id, acc],
            )
        elif title and not old_title:
            self.db.execute(
                "UPDATE chats SET title = ? WHERE chat_id = ? AND account_id = ?",
                [title, msg.chat_id, acc],
            )

    def _remember_group_card(self, msg: InboundMessage, card: str | None) -> None:
        """群名片单独存；people.name 永远保持 QQ 昵称。"""
        if not card:
            return
        kind = msg.chat_type.value if hasattr(msg.chat_type, "value") else str(msg.chat_type)
        if kind != "group":
            return
        group_id = msg.group_id or (msg.chat_id[6:] if msg.chat_id.startswith("group:") else "")
        if not group_id:
            return
        self.db.execute(
            """INSERT INTO person_group_names
               (qq, account_id, group_id, card, first_seen, last_seen)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(qq, account_id, group_id) DO UPDATE SET
                 card=excluded.card,
                 first_seen=MIN(person_group_names.first_seen, excluded.first_seen),
                 last_seen=MAX(person_group_names.last_seen, excluded.last_seen)""",
            [msg.sender_qq, msg.account_id or "default", group_id, card,
             msg.event_at, msg.event_at],
        )

    def _ensure_person(self, msg: InboundMessage, now: float) -> None:
        acc = msg.account_id or "default"
        row = self.db.query_one(
            "SELECT name, nickname_hist, card_hist FROM people WHERE qq = ? AND account_id = ?",
            [msg.sender_qq, acc],
        )
        nick = msg.sender_nick
        card = msg.sender_card

        if row is None:
            self.db.execute(
                """
                INSERT INTO people (
                  qq, account_id, platform, name, nickname_hist, card_hist,
                  msg_count, first_seen, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                [
                    msg.sender_qq, acc, msg.platform, nick,
                    json.dumps([{"name": nick, "from": msg.event_at, "to": None}], ensure_ascii=False) if nick else None,
                    json.dumps([{"card": card, "chat_id": msg.chat_id, "from": msg.event_at, "to": None}], ensure_ascii=False) if card else None,
                    0, msg.event_at, msg.event_at,
                ],
            )
            self._remember_group_card(msg, card)
            return

        # 昵称变了记一笔
        if nick and nick != row["name"]:
            hist = json.loads(row["nickname_hist"] or "[]")
            for h in hist:
                if h.get("to") is None:
                    h["to"] = msg.event_at
                    break
            hist.append({"name": nick, "from": msg.event_at, "to": None})
            self.db.execute(
                "UPDATE people SET name = ?, nickname_hist = ? WHERE qq = ? AND account_id = ?",
                [nick, json.dumps(hist, ensure_ascii=False), msg.sender_qq, msg.account_id],
            )

        # 群名片变了记一笔
        if card:
            cards = json.loads(row["card_hist"] or "[]")
            cur = next((c for c in cards if c.get("chat_id") == msg.chat_id and c.get("to") is None), None)
            if cur is None:
                cards.append({"card": card, "chat_id": msg.chat_id, "from": msg.event_at, "to": None})
                self.db.execute(
                    "UPDATE people SET card_hist = ? WHERE qq = ? AND account_id = ?",
                    [json.dumps(cards, ensure_ascii=False), msg.sender_qq, msg.account_id],
                )
            elif cur["card"] != card:
                cur["to"] = msg.event_at
                cards.append({"card": card, "chat_id": msg.chat_id, "from": msg.event_at, "to": None})
                self.db.execute(
                    "UPDATE people SET card_hist = ? WHERE qq = ? AND account_id = ?",
                    [json.dumps(cards, ensure_ascii=False), msg.sender_qq, msg.account_id],
                )

        # 群名片单独存，不覆盖 people.name（name 永远是 QQ 昵称）
        self._remember_group_card(msg, card)

    # -- 读取 ------------------------------------------------------------

    def get(self, ep_id: int) -> Episode | None:
        row = self.db.query_one("SELECT * FROM episodes WHERE id = ?", [ep_id])
        return _row_to_episode(row) if row else None

    def get_many(self, ep_ids: list[int]) -> list[Episode]:
        if not ep_ids:
            return []
        marks = ",".join("?" * len(ep_ids))
        rows = self.db.query(f"SELECT * FROM episodes WHERE id IN ({marks}) ORDER BY event_at", ep_ids)
        return [_row_to_episode(r) for r in rows]

    def search(
        self,
        *,
        chat_id: str | None = None,
        sender_qq: str | None = None,
        group_id: str | None = None,
        account_id: str | None = None,
        since: float | None = None,
        until: float | None = None,
        keyword: str | None = None,
        limit: int = 50,
        alive_only: bool = True,
    ) -> list[Episode]:
        where: list[str] = []
        params: list = []

        if alive_only:
            where.append("gone = 0")
        if chat_id:
            where.append("chat_id = ?")
            params.append(chat_id)
        if sender_qq:
            where.append("sender_qq = ?")
            params.append(sender_qq)
        if group_id:
            where.append("group_id = ?")
            params.append(group_id)
        if account_id:
            where.append("account_id = ?")
            params.append(account_id)
        if since is not None:
            where.append("event_at >= ?")
            params.append(since)
        if until is not None:
            where.append("event_at <= ?")
            params.append(until)

        if keyword:
            clause = " AND ".join(where) if where else "1=1"
            # 中文检索走双通道：
            #   FTS5 trigram 最小 3 字符，负责长词（精确、带排序）
            #   LIKE 兜底两字词（"老林""杭州"这种人名地名）
            #   词更长时 FTS 优先，结果不够再补 LIKE
            if self.has_fts and len(keyword) >= 3:
                sql = f"""
                  SELECT e.* FROM episodes e
                  JOIN episodes_fts f ON f.rowid = e.id
                  WHERE f.text MATCH ? AND {clause}
                  ORDER BY e.event_at DESC LIMIT ?
                """
                # FTS5 的 MATCH 语法特殊字符会直接报错（引号、AND、OR、NEAR、* 等）
                # 用双引号包起来当短语搜，内部的双引号再转义
                safe_kw = '"' + keyword.replace('"', '""') + '"'
                try:
                    rows = self.db.query(sql, [safe_kw, *params, limit])
                except Exception as e:
                    log.debug("FTS 查询失败（%s），退回 LIKE", e)
                    like_sql = (
                        f"SELECT * FROM episodes WHERE {clause} AND text LIKE ? "
                        f"ORDER BY event_at DESC LIMIT ?"
                    )
                    rows = self.db.query(like_sql, [*params, f"%{keyword}%", limit])
                # 不够就补一遍 LIKE
                if len(rows) < max(3, limit // 3):
                    seen = {r["id"] for r in rows}
                    like_sql = (
                        f"SELECT * FROM episodes WHERE {clause} AND text LIKE ? "
                        f"ORDER BY event_at DESC LIMIT ?"
                    )
                    extra = self.db.query(like_sql, [*params, f"%{keyword}%", limit])
                    rows = rows + [r for r in extra if r["id"] not in seen]
            else:
                sql = (
                    f"SELECT * FROM episodes WHERE {clause} AND text LIKE ? "
                    f"ORDER BY event_at DESC LIMIT ?"
                )
                rows = self.db.query(sql, [*params, f"%{keyword}%", limit])
        else:
            clause = " AND ".join(where) if where else "1=1"
            sql = f"SELECT * FROM episodes WHERE {clause} ORDER BY event_at DESC LIMIT ?"
            rows = self.db.query(sql, [*params, limit])

        return [_row_to_episode(r) for r in rows]

    def recent(self, chat_id: str, limit: int = 20, *, account_id: str = "") -> list[Episode]:
        sql = "SELECT * FROM episodes WHERE chat_id = ? AND gone = 0"
        params: list = [chat_id]
        if account_id:
            sql += " AND account_id = ?"
            params.append(account_id)
        sql += " ORDER BY event_at DESC LIMIT ?"
        params.append(limit)
        rows = self.db.query(sql, params)
        return [_row_to_episode(r) for r in reversed(rows)]

    def by_person(
        self, sender_qq: str, days: int = 7, limit: int = 30, *, account_id: str = ""
    ) -> list[Episode]:
        since = time.time() - days * 86400
        sql = (
            "SELECT * FROM episodes WHERE sender_qq = ? AND gone = 0 AND event_at >= ?"
        )
        params: list = [sender_qq, since]
        if account_id:
            sql += " AND account_id = ?"
            params.append(account_id)
        sql += " ORDER BY event_at DESC LIMIT ?"
        params.append(limit)
        rows = self.db.query(sql, params)
        return [_row_to_episode(r) for r in rows]

    # -- 状态 ------------------------------------------------------------

    def mark_distilled(self, ep_ids: list[int]) -> int:
        if not ep_ids:
            return 0
        marks = ",".join("?" * len(ep_ids))
        return self.db.execute(f"UPDATE episodes SET distilled = 1 WHERE id IN ({marks})", ep_ids)

    def mark_distill_failed(self, ep_ids: list[int]) -> int:
        """蒸馏失败：计数 +1。超过上限就不再捞它，避免坏数据无限重试。"""
        if not ep_ids:
            return 0
        marks = ",".join("?" * len(ep_ids))
        return self.db.execute(
            f"UPDATE episodes SET distill_attempts = distill_attempts + 1 "
            f"WHERE id IN ({marks})",
            ep_ids,
        )

    def distill_candidates(
        self,
        limit: int = 20,
        *,
        min_age_sec: int = 60,
        max_attempts: int = 3,
    ) -> list[Episode]:
        """该蒸馏但还没蒸的，跨会话捞一批。

        留一点冷却时间（min_age_sec），避免半句话被蒸掉。
        """
        cutoff = time.time() - min_age_sec
        rows = self.db.query(
            "SELECT * FROM episodes WHERE distilled = 0 AND gone = 0 "
            "AND event_at <= ? AND distill_attempts < ? "
            "ORDER BY event_at LIMIT ?",
            [cutoff, max_attempts, limit],
        )
        return [_row_to_episode(r) for r in rows]

    def unprocessed(self, chat_id: str, limit: int = 20, min_age_sec: int = 60) -> list[Episode]:
        """某个会话里该蒸但还没蒸的。留一点冷却时间，避免半句被蒸掉。"""
        cutoff = time.time() - min_age_sec
        rows = self.db.query(
            "SELECT * FROM episodes WHERE chat_id = ? AND distilled = 0 "
            "AND gone = 0 AND event_at <= ? AND distill_attempts < 3 "
            "ORDER BY event_at LIMIT ?",
            [chat_id, cutoff, limit],
        )
        return [_row_to_episode(r) for r in rows]

    def expire(self, days: int | None = None) -> int:
        """第一阶段：标过期（假删）。查询看不见了，但还在库里。

        再过 grace_days 天由 purge() 真正删掉。
        """
        days = days if days is not None else self.retention_days
        cutoff = time.time() - days * 86400
        return self.db.execute(
            "UPDATE episodes SET gone = 1, gone_reason = 'expired', gone_at = ? "
            "WHERE gone = 0 AND event_at < ?",
            [time.time(), cutoff],
        )

    def purge(self, grace_days: int | None = None) -> int:
        """第二阶段：真删。

        已经标了 gone、且过期又超过宽限期的，物理删除。
        宽限期默认跟保留期一样长——标 gone 之后再留 7 天，然后真删。
        """
        grace = self.retention_days if grace_days is None else grace_days
        cutoff = time.time() - grace * 86400

        # 宽限期从「标记时间」算，不是从「消息时间」算。
        # 用消息时间的话，本来就旧的消息一标就立刻超期，等于没宽限。
        rows = self.db.query(
            "SELECT id FROM episodes WHERE gone = 1 AND COALESCE(gone_at, event_at) < ? LIMIT 1000",
            [cutoff],
        )
        if not rows:
            return 0

        ids = [r["id"] for r in rows]
        marks = ",".join("?" * len(ids))

        # emotions 有外键 ON DELETE CASCADE，删 episodes 会带走
        n = self.db.execute(f"DELETE FROM episodes WHERE id IN ({marks})", ids)
        log.info("物理删除 %d 条过期消息", n)
        return n

    def purge_all(self, grace_days: int | None = None, *, batch: int = 20) -> int:
        """反复调用 purge 直到删完。适合定时任务。"""
        total = 0
        for _ in range(batch):
            n = self.purge(grace_days)
            total += n
            if n == 0:
                break
        return total

    def vacuum_if_needed(self, *, min_free_kb: int = 10240) -> bool:
        """删得够多了就整理一下数据库文件。

        VACUUM 会重建整个文件，比较慢，别频繁跑。
        """
        try:
            freelist = self.db.scalar("PRAGMA freelist_count", default=0) or 0
            page_size = self.db.scalar("PRAGMA page_size", default=4096) or 4096
            free_kb = freelist * page_size / 1024
            if free_kb >= min_free_kb:
                log.info("空闲页 %.0f KB，执行 VACUUM", free_kb)
                self.db.vacuum()
                return True
        except Exception as e:
            log.warning("VACUUM 失败：%s", e)
        return False

    def count(self, *, chat_id: str | None = None, alive_only: bool = True) -> int:
        where = []
        params: list = []
        if alive_only:
            where.append("gone = 0")
        if chat_id:
            where.append("chat_id = ?")
            params.append(chat_id)
        clause = " AND ".join(where) if where else "1=1"
        return self.db.scalar(f"SELECT COUNT(*) FROM episodes WHERE {clause}", params, default=0)

    # -- 辅助 ------------------------------------------------------------

    @staticmethod
    def _level_value(level) -> int:
        if hasattr(level, "value"):
            level = level.value
        return {"member": 0, "admin": 1, "owner": 2}.get(str(level), 0)


def _row_to_episode(row) -> Episode:
    return Episode(
        id=row["id"],
        account_id=row["account_id"],
        platform=row["platform"],
        chat_id=row["chat_id"],
        chat_type=row["chat_type"],
        group_id=row["group_id"],
        group_name=row["group_name"],
        sender_qq=row["sender_qq"],
        sender_nick=row["sender_nick"],
        sender_card=row["sender_card"],
        sender_level=row["sender_level"] or 0,
        text=row["text"] or "",
        raw=row["raw"],
        msg_type=row["msg_type"] or "text",
        reply_to=row["reply_to"],
        at_list=row["at_list"],
        event_at=row["event_at"],
        recv_at=row["recv_at"],
        distilled=row["distilled"] or 0,
        gone=row["gone"] or 0,
    )
