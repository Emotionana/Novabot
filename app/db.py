"""数据库连接与基础封装。

裸 sqlite3 + 手写 SQL。不用 ORM——记忆系统的查询太定制。
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import config


class Database:
    """一个连接的薄封装。

    单进程单写者场景，用 threading.local 保证每个线程一个连接。
    """

    def __init__(self, path: Path | str, *, mmap_mb: int = 256, busy_timeout_ms: int = 5000):
        self.path = Path(path)
        self.mmap_mb = mmap_mb
        self.busy_timeout_ms = busy_timeout_ms
        self._local = threading.local()
        self._conns: set[sqlite3.Connection] = set()
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- 连接管理 --------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,  # 自己管事务
            check_same_thread=False,
        )
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("PRAGMA journal_mode = WAL")
        cur.execute("PRAGMA synchronous = NORMAL")
        cur.execute("PRAGMA foreign_keys = ON")
        cur.execute("PRAGMA temp_store = MEMORY")
        cur.execute(f"PRAGMA mmap_size = {self.mmap_mb * 1024 * 1024}")
        cur.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        cur.close()
        with self._lock:
            self._conns.add(con)
        return con

    @property
    def con(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = self._connect()
            self._local.con = con
        return con

    def close(self) -> None:
        """关掉所有线程的连接。

        FastAPI 的同步接口会在线程池不同线程里建连接；只关当前线程那条，
        进程退出时文件可能还被占着。这里统一收。
        """
        con = getattr(self._local, "con", None)
        if con is not None:
            self._local.con = None
        with self._lock:
            conns = list(self._conns)
            self._conns.clear()
        for c in conns:
            with contextlib.suppress(Exception):
                c.close()

    # -- 查询 ------------------------------------------------------------

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        cur = self.con.execute(sql, tuple(params))
        try:
            return cur.fetchall()
        finally:
            cur.close()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        cur = self.con.execute(sql, tuple(params))
        try:
            return cur.fetchone()
        finally:
            cur.close()

    def scalar(self, sql: str, params: Iterable[Any] = (), default: Any = None) -> Any:
        row = self.query_one(sql, params)
        if row is None:
            return default
        return row[0]

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        cur = self.con.execute(sql, tuple(params))
        try:
            return cur.rowcount
        finally:
            cur.close()

    def executemany(self, sql: str, seq: Iterable[Iterable[Any]]) -> int:
        cur = self.con.executemany(sql, [tuple(x) for x in seq])
        try:
            return cur.rowcount
        finally:
            cur.close()

    def insert(self, sql: str, params: Iterable[Any] = ()) -> int:
        cur = self.con.execute(sql, tuple(params))
        try:
            return cur.lastrowid or 0
        finally:
            cur.close()

    # -- 事务 ------------------------------------------------------------

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        con = self.con
        con.execute("BEGIN")
        try:
            yield con
        except Exception:
            con.execute("ROLLBACK")
            raise
        else:
            con.execute("COMMIT")

    # -- 维护 ------------------------------------------------------------

    def backup(self, target: Path | str) -> None:
        """在线备份。WAL 模式下不能用文件复制。"""
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        dst = sqlite3.connect(target)
        try:
            self.con.backup(dst)
        finally:
            dst.close()

    def optimize(self) -> None:
        self.con.execute("PRAGMA optimize")

    def vacuum(self) -> None:
        self.con.execute("VACUUM")

    def checkpoint(self, mode: str = "TRUNCATE") -> None:
        self.con.execute(f"PRAGMA wal_checkpoint({mode})")

    def table_exists(self, name: str) -> bool:
        row = self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
            [name],
        )
        return row is not None

    def schema_version(self) -> int:
        try:
            v = self.scalar("SELECT v FROM meta WHERE k = 'schema_version'")
        except sqlite3.OperationalError:
            return 0
        return int(v) if v else 0

    def set_schema_version(self, version: int) -> None:
        import time

        self.execute(
            "INSERT OR REPLACE INTO meta (k, v, updated_at) VALUES ('schema_version', ?, ?)",
            [str(version), time.time()],
        )


# ---------------------------------------------------------------------------
# 全局实例
# ---------------------------------------------------------------------------

_db: Database | None = None
_lock = threading.Lock()


def get_db() -> Database:
    global _db
    if _db is None:
        with _lock:
            if _db is None:
                cfg = config.get()
                _db = Database(cfg.db_path(), mmap_mb=cfg.database.mmap_mb)
    return _db


def reset_db(db: Database | None = None) -> None:
    """测试用：换一个实例。"""
    global _db
    _db = db
