"""线程池连接统一关闭。"""

from __future__ import annotations

import threading

from app.db import Database
from app.memory.migrate import init_schema


def test_close_all_thread_connections(tmp_path):
    db = Database(tmp_path / "x.db")
    init_schema(db)

    def worker():
        db.query("SELECT COUNT(*) FROM episodes")

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    db.close()
    # 连接真的释放了，文件才能改名/删除
    db.path.unlink()
    assert not db.path.exists()
