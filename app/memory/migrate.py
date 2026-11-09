"""建表与迁移。

schema.sql 是"当前版本"的完整定义，幂等（CREATE IF NOT EXISTS）。
老库靠 MIGRATIONS 一步步升上来，**必须按版本顺序执行**。
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable
from pathlib import Path

from ..db import Database

log = logging.getLogger(__name__)

SCHEMA_VERSION = 16
SCHEMA_FILE = Path(__file__).parent / "schema.sql"


# 账号管理需要的列（v5）。既要进 schema.sql（新库），也要能加到老库上。
ACCOUNT_COLUMNS = (
    ("impl", "TEXT DEFAULT 'snowluma'"),
    ("reverse_mode", "INTEGER NOT NULL DEFAULT 0"),
    ("url", "TEXT"),
    ("listen", "TEXT"),
    ("token", "TEXT"),
    ("auto_start", "INTEGER NOT NULL DEFAULT 1"),
    ("onebot_port", "INTEGER NOT NULL DEFAULT 0"),
    ("snowluma_webui_port", "INTEGER NOT NULL DEFAULT 0"),
    ("snowluma_novnc_port", "INTEGER NOT NULL DEFAULT 0"),
    ("sort_order", "INTEGER NOT NULL DEFAULT 0"),
    ("locked", "INTEGER NOT NULL DEFAULT 0"),
)


def _migrate_v5(db: Database) -> None:
    """账号表补上多开需要的列。已有的列跳过（老版本可能运行时加过）。"""
    for col, typ in ACCOUNT_COLUMNS:
        with contextlib.suppress(Exception):
            db.execute(f"ALTER TABLE accounts ADD COLUMN {col} {typ}")


# chats 重建用的列（v6）
CHAT_COLUMNS = (
    "chat_id, account_id, platform, chat_type, group_id, title, title_history, "
    "owner_qq, admin_list, member_count, vibe, topics, impression, msg_count, "
    "first_seen, last_seen"
)


def _migrate_v14(db: Database) -> None:
    """群名片独立表 + 旧数据回填；people.name 保持 QQ 昵称。"""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS person_group_names (
          qq          TEXT NOT NULL,
          account_id  TEXT NOT NULL,
          group_id    TEXT NOT NULL,
          card        TEXT NOT NULL,
          first_seen  REAL NOT NULL,
          last_seen   REAL NOT NULL,
          PRIMARY KEY (qq, account_id, group_id)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_pgn_qq ON person_group_names(qq, account_id, last_seen DESC)"
    )
    # 从历史群消息回填群名片
    db.execute(
        """
        INSERT INTO person_group_names (qq, account_id, group_id, card, first_seen, last_seen)
        SELECT sender_qq, account_id, group_id, sender_card,
               MIN(event_at), MAX(event_at)
        FROM episodes
        WHERE chat_type = 'group' AND sender_card <> '' AND sender_qq <> ''
          AND sender_qq <> 'self' AND group_id IS NOT NULL
        GROUP BY sender_qq, account_id, group_id, sender_card
        ON CONFLICT(qq, account_id, group_id) DO UPDATE SET
          card = excluded.card,
          first_seen = MIN(person_group_names.first_seen, excluded.first_seen),
          last_seen = MAX(person_group_names.last_seen, excluded.last_seen)
        """
    )
    # 旧 card_hist 也回填（历史导入的数据）
    for row in db.query("SELECT qq, account_id, card_hist FROM people WHERE card_hist IS NOT NULL"):
        try:
            cards = __import__("json").loads(row["card_hist"] or "[]")
        except Exception:  # noqa: BLE001
            continue
        for c in cards:
            card = str(c.get("card") or "").strip()
            chat_id = str(c.get("chat_id") or "")
            group_id = chat_id.split(":", 1)[1] if chat_id.startswith("group:") else ""
            if not card or not group_id:
                continue
            at = float(c.get("from") or c.get("to") or 0)
            db.execute(
                """INSERT INTO person_group_names (qq, account_id, group_id, card, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(qq, account_id, group_id) DO UPDATE SET
                     card=excluded.card,
                     first_seen=MIN(person_group_names.first_seen, excluded.first_seen),
                     last_seen=MAX(person_group_names.last_seen, excluded.last_seen)""",
                [row["qq"], row["account_id"], group_id, card, at or 0, at or 0],
            )
    # 旧版本可能把群名片写进了 people.name；只在这张卡确实来自群名片时纠正
    for row in db.query("SELECT qq, account_id, name FROM people"):
        name = str(row["name"] or "").strip()
        if not name:
            continue
        hit = db.scalar(
            "SELECT 1 FROM person_group_names WHERE qq=? AND account_id=? AND card=? LIMIT 1",
            [row["qq"], row["account_id"], name],
        )
        if not hit:
            continue
        nick = db.scalar(
            """SELECT sender_nick FROM episodes
               WHERE sender_qq=? AND account_id=? AND sender_nick<>'' AND sender_nick IS NOT NULL
               GROUP BY sender_nick ORDER BY COUNT(*) DESC LIMIT 1""",
            [row["qq"], row["account_id"]],
        )
        if nick and nick != name:
            db.execute(
                "UPDATE people SET name=?, updated_at=? WHERE qq=? AND account_id=?",
                [nick, __import__("time").time(), row["qq"], row["account_id"]],
            )


def _migrate_v13(db: Database) -> None:
    """外部 agent 平台会话映射。"""
    for sql in (
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
        """,
        "CREATE INDEX IF NOT EXISTS idx_ps_chat ON platform_sessions(chat_id)",
    ):
        db.execute(sql)


def _migrate_v12(db: Database) -> None:
    """语音引擎面板配置（引擎从 config 播种，之后以数据库为准）。"""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS voice_engines (
          id          TEXT PRIMARY KEY,
          name        TEXT NOT NULL,
          type        TEXT NOT NULL DEFAULT 'gptsovits',
          url         TEXT NOT NULL DEFAULT '',
          api_key     TEXT NOT NULL DEFAULT '',
          model       TEXT NOT NULL DEFAULT '',
          voice       TEXT NOT NULL DEFAULT '',
          timeout     REAL NOT NULL DEFAULT 120,
          defaults    TEXT NOT NULL DEFAULT '{}',
          enabled     INTEGER NOT NULL DEFAULT 1,
          created_at  REAL NOT NULL,
          updated_at  REAL NOT NULL
        )
        """
    )


def _migrate_v11(db: Database) -> None:
    """文档知识库：文档 + 切片。"""
    for sql in (
        """
        CREATE TABLE IF NOT EXISTS documents (
          id          INTEGER PRIMARY KEY,
          account_id  TEXT NOT NULL DEFAULT '',
          title       TEXT NOT NULL,
          source      TEXT DEFAULT '',
          text        TEXT NOT NULL,
          created_at  REAL NOT NULL,
          updated_at  REAL NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_docs_account ON documents(account_id, created_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS doc_chunks (
          id          INTEGER PRIMARY KEY,
          document_id INTEGER NOT NULL,
          chunk_index INTEGER NOT NULL,
          text        TEXT NOT NULL,
          line_start  INTEGER DEFAULT 0,
          line_end    INTEGER DEFAULT 0,
          created_at  REAL NOT NULL,
          FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_chunks_doc ON doc_chunks(document_id, chunk_index)",
    ):
        db.execute(sql)


def _migrate_v10(db: Database) -> None:
    """工作向：笔记、提醒、工具调用审计。"""
    for sql in (
        """
        CREATE TABLE IF NOT EXISTS notes (
          id          INTEGER PRIMARY KEY,
          account_id  TEXT NOT NULL,
          person_qq   TEXT NOT NULL,
          chat_id     TEXT,
          title       TEXT,
          body        TEXT NOT NULL,
          tags        TEXT DEFAULT '[]',
          created_at  REAL NOT NULL,
          updated_at  REAL NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_notes_account ON notes(account_id, created_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS reminders (
          id          INTEGER PRIMARY KEY,
          account_id  TEXT NOT NULL,
          person_qq   TEXT NOT NULL,
          chat_id     TEXT NOT NULL,
          text        TEXT NOT NULL,
          due_at      REAL NOT NULL,
          status      TEXT NOT NULL DEFAULT 'pending',
          attempts    INTEGER NOT NULL DEFAULT 0,
          created_at  REAL NOT NULL,
          sent_at     REAL,
          error       TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, due_at)",
        "CREATE INDEX IF NOT EXISTS idx_reminders_person "
        "ON reminders(account_id, person_qq, created_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS tool_calls (
          id          INTEGER PRIMARY KEY,
          account_id  TEXT NOT NULL,
          chat_id     TEXT,
          person_qq   TEXT,
          tool        TEXT NOT NULL,
          args        TEXT,
          result      TEXT,
          ok          INTEGER NOT NULL DEFAULT 1,
          duration_ms INTEGER NOT NULL DEFAULT 0,
          created_at  REAL NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_tool_calls_time ON tool_calls(created_at DESC)",
    ):
        db.execute(sql)


def _migrate_v9(db: Database) -> None:
    """画像和指代消解收编进 schema（以前是运行时 ALTER/CREATE）。"""
    cols = (
        ("age_group", "TEXT"), ("occupation", "TEXT"), ("location", "TEXT"),
        ("sleep_pattern", "TEXT"), ("family", "TEXT"), ("speech_pattern", "TEXT"),
        ("mood_baseline", "REAL"), ("sensitive_topics", "TEXT"),
        ("trust_level", "INTEGER DEFAULT 0"), ("last_summary", "TEXT"),
        ("updated_at", "REAL"),
    )
    for name, typ in cols:
        with contextlib.suppress(Exception):
            db.execute(f"ALTER TABLE people ADD COLUMN {name} {typ}")

    for sql in (
        """
        CREATE TABLE IF NOT EXISTS person_interests (
          qq          TEXT NOT NULL,
          account_id  TEXT NOT NULL,
          topic       TEXT NOT NULL,
          kind        TEXT NOT NULL DEFAULT 'like',
          weight      REAL NOT NULL DEFAULT 0.5,
          last_mention REAL,
          evidence    TEXT,
          created_at  REAL NOT NULL,
          PRIMARY KEY (qq, account_id, topic, kind)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_pi_qq "
        "ON person_interests(qq, account_id, kind, weight DESC)",
        """
        CREATE TABLE IF NOT EXISTS profile_history (
          id         INTEGER PRIMARY KEY,
          qq         TEXT NOT NULL,
          account_id TEXT NOT NULL,
          field      TEXT NOT NULL,
          old_value  TEXT,
          new_value  TEXT,
          changed_at REAL NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_ph_qq ON profile_history(qq, changed_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS person_aliases (
          alias      TEXT NOT NULL,
          qq         TEXT NOT NULL,
          account_id TEXT NOT NULL,
          source     TEXT NOT NULL DEFAULT 'nickname',
          chat_id    TEXT,
          weight     REAL NOT NULL DEFAULT 1.0,
          first_seen REAL NOT NULL,
          last_seen  REAL NOT NULL,
          PRIMARY KEY (alias, qq, account_id, chat_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_alias_lookup ON person_aliases(alias, account_id)",
        """
        CREATE TABLE IF NOT EXISTS pronoun_resolutions (
          id          INTEGER PRIMARY KEY,
          chat_id     TEXT NOT NULL,
          episode_id  INTEGER,
          pronoun     TEXT,
          resolved_to TEXT,
          method      TEXT,
          confidence  REAL,
          created_at  REAL NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS name_ambiguities (
          id          INTEGER PRIMARY KEY,
          alias       TEXT NOT NULL,
          chat_id     TEXT,
          candidates  TEXT,
          resolved_to TEXT,
          created_at  REAL NOT NULL
        )
        """,
    ):
        db.execute(sql)


def _migrate_v8(db: Database) -> None:
    """主动私聊：每人状态 + 发送日志。"""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS proactive_state (
          account_id      TEXT NOT NULL,
          person_qq       TEXT NOT NULL,
          last_sent_at    REAL,
          last_reply_at   REAL,
          ignored_streak  INTEGER NOT NULL DEFAULT 0,
          next_eligible_at REAL,
          daily_date      TEXT,
          daily_count     INTEGER NOT NULL DEFAULT 0,
          last_score      REAL,
          last_status     TEXT,
          updated_at      REAL NOT NULL,
          PRIMARY KEY (account_id, person_qq)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS proactive_log (
          id          INTEGER PRIMARY KEY,
          account_id  TEXT NOT NULL,
          person_qq   TEXT NOT NULL,
          person_name TEXT,
          sent_at     REAL NOT NULL,
          message     TEXT NOT NULL,
          topic       TEXT,
          score       REAL,
          reason      TEXT,
          replied_at  REAL,
          reply_text  TEXT,
          status      TEXT NOT NULL DEFAULT 'sent',
          error       TEXT,
          created_at  REAL NOT NULL
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_proactive_person "
        "ON proactive_log(account_id, person_qq, sent_at DESC)"
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_proactive_sent ON proactive_log(sent_at DESC)")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_ep_sender_time "
        "ON episodes(account_id, sender_qq, event_at DESC)"
    )


def _migrate_v7(db: Database) -> None:
    """voice_refs 去掉对 persona_cards 的外键。

    参考音可以是"全局"的（persona_id='')，没有角色卡也能用语音；
    有外键时空串过不了约束，所以整表重建。
    """
    db.execute("DROP TABLE IF EXISTS voice_refs_v7")
    db.execute(
        """
        CREATE TABLE voice_refs_v7 (
          id          TEXT PRIMARY KEY,
          persona_id  TEXT NOT NULL DEFAULT '',
          name        TEXT NOT NULL,
          engine      TEXT NOT NULL DEFAULT 'gptsovits',
          audio_path  TEXT NOT NULL,
          duration    REAL,
          prompt_text TEXT NOT NULL DEFAULT '',
          params      TEXT NOT NULL DEFAULT '{}',
          weight      REAL NOT NULL DEFAULT 1.0,
          tags        TEXT DEFAULT '[]',
          min_chars   INTEGER DEFAULT 0,
          max_chars   INTEGER DEFAULT 9999,
          emotions    TEXT DEFAULT '[]',
          condition   TEXT,
          enabled     INTEGER NOT NULL DEFAULT 1,
          note        TEXT,
          created_at  REAL NOT NULL,
          updated_at  REAL NOT NULL
        )
        """
    )
    db.execute(
        "INSERT OR IGNORE INTO voice_refs_v7 "
        "(id, persona_id, name, engine, audio_path, duration, prompt_text, params, weight, "
        " tags, min_chars, max_chars, emotions, condition, enabled, note, created_at, updated_at) "
        "SELECT id, COALESCE(persona_id, ''), name, engine, audio_path, duration, prompt_text, "
        " params, weight, tags, min_chars, max_chars, emotions, condition, enabled, note, "
        " created_at, updated_at FROM voice_refs"
    )
    db.execute("DROP TABLE voice_refs")
    db.execute("ALTER TABLE voice_refs_v7 RENAME TO voice_refs")
    db.execute("CREATE INDEX IF NOT EXISTS idx_vr_persona ON voice_refs(persona_id, enabled)")


def _migrate_v6(db: Database) -> None:
    """多开：同一个群号在不同账号下必须各占一行。

    老表主键只有 chat_id，两个号进同一个群会互相覆盖标题和消息数。
    顺手删掉没人用的 sessions（它挡着 chats 重建，且代码里从未读写）。
    """
    db.execute("DROP TABLE IF EXISTS sessions")
    db.execute("DROP TABLE IF EXISTS chats_v6")
    db.execute(
        """
        CREATE TABLE chats_v6 (
          chat_id        TEXT NOT NULL,
          account_id     TEXT NOT NULL,
          platform       TEXT NOT NULL DEFAULT 'qq',
          chat_type      TEXT NOT NULL DEFAULT 'private',
          group_id       TEXT,
          title          TEXT,
          title_history  TEXT,
          owner_qq       TEXT,
          admin_list     TEXT,
          member_count   INTEGER,
          vibe           TEXT,
          topics         TEXT,
          impression     TEXT,
          msg_count      INTEGER NOT NULL DEFAULT 0,
          first_seen     REAL NOT NULL,
          last_seen      REAL NOT NULL,
          PRIMARY KEY (account_id, chat_id),
          FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
        )
        """
    )
    db.execute(f"INSERT OR IGNORE INTO chats_v6 ({CHAT_COLUMNS}) SELECT {CHAT_COLUMNS} FROM chats")
    db.execute("DROP TABLE chats")
    db.execute("ALTER TABLE chats_v6 RENAME TO chats")
    db.execute("CREATE INDEX IF NOT EXISTS idx_chats_account ON chats(account_id, last_seen DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_chats_group ON chats(group_id)")


def _migrate_v3(db: Database) -> None:
    """重建 episodes_au：只有正文变了才动全文索引。

    老 schema 的触发器不带 WHEN，标过期 / 标已蒸馏这种更新
    也会把整条 FTS 删掉重写，批量处理时慢一个量级。

    没有 FTS 表时跳过（检索本来就走 LIKE）。
    """
    if not db.table_exists("episodes_fts"):
        return
    db.execute("DROP TRIGGER IF EXISTS episodes_au")
    db.execute(
        """
        CREATE TRIGGER episodes_au AFTER UPDATE ON episodes
        WHEN old.text IS NOT new.text
        BEGIN
          INSERT INTO episodes_fts(episodes_fts, rowid, text) VALUES ('delete', old.id, old.text);
          INSERT INTO episodes_fts(rowid, text) VALUES (new.id, new.text);
        END
        """
    )


# 一个迁移步骤：一串 SQL，或者一个函数（要做判断时用函数）
MigrationStep = list[str] | Callable[[Database], None]

def _migrate_v15(db: Database) -> None:
    """人格卡头像列；重复执行也安全。"""
    cols = {r["name"] for r in db.query("PRAGMA table_info(persona_cards)")}
    if "avatar" not in cols:
        db.execute("ALTER TABLE persona_cards ADD COLUMN avatar TEXT")


def _migrate_v16(db: Database) -> None:
    """参考音/语音日志按账号隔离；旧数据保留为全局参考音（account_id 为空）。"""
    for table in ("voice_refs", "voice_logs"):
        cols = {r["name"] for r in db.query(f"PRAGMA table_info({table})")}
        if "account_id" not in cols:
            db.execute(f"ALTER TABLE {table} ADD COLUMN account_id TEXT NOT NULL DEFAULT ''")


MIGRATIONS: list[tuple[int, MigrationStep]] = [
    (2, ["ALTER TABLE episodes ADD COLUMN gone_at REAL"]),
    # v3 要判断 FTS 表在不在，SQL 表达不了
    (3, _migrate_v3),
    # v4：蒸馏失败计数（避免坏数据无限重试、无限烧钱）
    (4, ["ALTER TABLE episodes ADD COLUMN distill_attempts INTEGER NOT NULL DEFAULT 0"]),
    # v5：账号管理需要的列
    (5, _migrate_v5),
    # v6：chats 按 (account_id, chat_id) 分家
    (6, _migrate_v6),
    # v7：voice_refs 支持全局参考音（去掉 persona 外键）
    (7, _migrate_v7),
    # v8：主动私聊状态和日志
    (8, _migrate_v8),
    # v9：画像/指代消解进 schema
    (9, _migrate_v9),
    # v10：笔记 / 提醒 / 工具审计
    (10, _migrate_v10),
    # v11：文档知识库
    (11, _migrate_v11),
    # v12：语音引擎面板
    (12, _migrate_v12),
    # v13：平台会话映射
    (13, _migrate_v13),
    (14, _migrate_v14),
    (15, _migrate_v15),
    (16, _migrate_v16),
]


def _record_fts(db: Database) -> bool:
    """探测 FTS5 是否真的可用，并把结果写回 meta。

    不能只信 meta 里的旧值：老库从 v1/v2 升级上来时这个键可能压根不存在，
    那样会明明有索引却一直走 LIKE（现在线上就是这样）。
    """
    ok = has_fts5(db) and db.table_exists("episodes_fts")
    db.execute(
        "INSERT OR REPLACE INTO meta (k, v, updated_at) VALUES ('fts5_enabled', ?, ?)",
        ["1" if ok else "0", time.time()],
    )
    return ok


def has_fts5(db: Database) -> bool:
    """探测 SQLite 有没有编译 FTS5。

    Linux 上有的发行版为了省体积会关掉，没有的话建虚拟表会直接报错，
    整个 schema 初始化就挂了。
    """
    try:
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.__fts_probe USING fts5(x)")
        db.execute("DROP TABLE IF EXISTS temp.__fts_probe")
        return True
    except Exception:
        return False


def _schema_without_fts() -> str:
    """去掉 FTS 相关语句的 schema。

    没有 FTS5 时用这个建表，检索退回 LIKE。
    """
    import re

    sql = SCHEMA_FILE.read_text(encoding="utf-8")

    # 删掉 episodes_fts 虚表（从 CREATE VIRTUAL TABLE 到分号）
    sql = re.sub(
        r"CREATE VIRTUAL TABLE[\s\S]*?episodes_fts[\s\S]*?\);",
        "-- FTS5 不可用，跳过 episodes_fts",
        sql,
        flags=re.I,
    )
    # 删掉三个触发器（CREATE TRIGGER 到 END;）
    sql = re.sub(
        r"CREATE TRIGGER[\s\S]*?END;",
        "-- FTS5 不可用，跳过 TRIGGER",
        sql,
        flags=re.I,
    )
    return sql


def init_schema(db: Database, *, force: bool = False) -> None:
    """建表。已经建过就跳过（除非 force）。"""
    current = db.schema_version()

    if current == 0 or force:
        fts_ok = has_fts5(db)
        if fts_ok:
            sql = SCHEMA_FILE.read_text(encoding="utf-8")
            log.info("初始化 schema v%d（FTS5 可用）", SCHEMA_VERSION)
        else:
            sql = _schema_without_fts()
            log.warning("SQLite 没编译 FTS5，跳过全文索引表，检索会退回 LIKE（慢一些）")

        # sqlite3 的 executescript 不支持参数，但能跑多语句
        db.con.executescript(sql)

        # 记录版本
        db.execute(
            "INSERT OR REPLACE INTO meta (k, v, updated_at) VALUES ('schema_version', ?, ?)",
            [str(SCHEMA_VERSION), time.time()],
        )
        db.execute(
            "INSERT OR REPLACE INTO meta (k, v, updated_at) VALUES ('schema_init_at', ?, ?)",
            [str(time.time()), time.time()],
        )
        _record_fts(db)
        log.info("schema v%d 建好", SCHEMA_VERSION)
        return

    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库版本 {current} 高于代码版本 {SCHEMA_VERSION}，请升级代码"
        )

    # 重建式迁移前先备份：记忆是这个项目唯一不可再生的数据
    backup_path = None
    if current < SCHEMA_VERSION:
        backups = Path(db.path).parent / "backups"
        backup_path = backups / f"pre-v{current}-{time.strftime('%Y%m%d-%H%M%S')}.db"
        try:
            db.backup(backup_path)
            log.info("迁移前已备份：%s", backup_path)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"迁移前备份失败，已停止迁移：{e}") from e

    # 逐版本迁移（SQL 和函数混排，顺序就是版本顺序）
    for target, step in MIGRATIONS:
        if current >= target:
            continue
        log.info("迁移到 v%d", target)
        with db.tx():
            if callable(step):
                step(db)
            else:
                for stmt in step:
                    db.execute(stmt)
            db.execute(
                "INSERT OR REPLACE INTO meta (k, v, updated_at) VALUES ('schema_version', ?, ?)",
                [str(target), time.time()],
            )
        current = target

    # 每次启动都重新探测，别信历史记录
    if not _record_fts(db):
        log.info("当前库没有可用的 FTS5 全文索引，检索走 LIKE")

    if current != SCHEMA_VERSION:
        log.warning("迁移后版本 %d，代码期望 %d", current, SCHEMA_VERSION)


def table_counts(db: Database) -> dict[str, int]:
    """各表行数，调试用。"""
    tables = (
        # 账号会话
        "accounts", "chats",
        # 海马
        "episodes",
        # 杏仁核
        "emotions", "emotion_baseline",
        # 新皮层
        "facts", "gists", "people",
        # 图
        "nodes", "edges",
        # 自我
        "self_profile", "self_events", "feedback",
        # 人格
        "persona_cards", "persona_keywords", "persona_state", "person_group_names",
        "keyword_fires", "persona_raw_blocks",
        # 语音
        "voice_refs", "voice_logs",
        # 画像 / 指代
        "person_aliases", "person_interests", "profile_history",
        "pronoun_resolutions", "name_ambiguities",
        # 演化
        "trends", "compressions",
        # 向量
        "vectors",
        # 工作向
        "notes", "reminders", "tool_calls", "documents", "doc_chunks",
        # 运行
        "pending_distill", "jobs", "audit", "settings",
        # 生活
        "life_days", "life_events",
    )
    out: dict[str, int] = {}
    for t in tables:
        if db.table_exists(t):
            out[t] = db.scalar(f"SELECT COUNT(*) FROM {t}", default=0)
    return out
