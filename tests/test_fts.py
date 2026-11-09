"""FTS：可用性探测与更新触发器。"""

from __future__ import annotations

from app.memory.migrate import init_schema
from tests.helpers import group_msg


def test_hippocampus_sees_fts(db):
    from app.memory.hippocampus import Hippocampus

    assert Hippocampus(db).has_fts is True


def test_meta_has_fts_flag(db):
    assert db.scalar("SELECT v FROM meta WHERE k = 'fts5_enabled'") == "1"


def test_missing_flag_is_repaired_on_startup(db):
    """老库从 v1/v2 升上来时这个键可能不存在，启动要补上。"""
    db.execute("DELETE FROM meta WHERE k = 'fts5_enabled'")

    init_schema(db)

    assert db.scalar("SELECT v FROM meta WHERE k = 'fts5_enabled'") == "1"


def test_non_text_update_does_not_touch_index(db, mem):
    ep = mem.remember(group_msg("杭州天气不错啊"))
    before = db.scalar("SELECT COUNT(*) FROM episodes_fts")

    db.execute("UPDATE episodes SET distilled = 1, gone_at = ? WHERE id = ?", [1.0, ep])

    assert db.scalar("SELECT COUNT(*) FROM episodes_fts") == before
    rows = db.query(
        "SELECT rowid FROM episodes_fts WHERE episodes_fts MATCH ?", ['"杭州天气"']
    )
    assert [r["rowid"] for r in rows] == [ep]


def test_text_update_reindexes(db, mem):
    ep = mem.remember(group_msg("杭州天气不错啊"))

    db.execute("UPDATE episodes SET text = ? WHERE id = ?", ["成都天气不错啊", ep])

    old = db.query(
        "SELECT rowid FROM episodes_fts WHERE episodes_fts MATCH ?", ['"杭州天气"']
    )
    new = db.query(
        "SELECT rowid FROM episodes_fts WHERE episodes_fts MATCH ?", ['"成都天气"']
    )
    assert old == [], "改掉的正文不该还能搜到"
    assert [r["rowid"] for r in new] == [ep]


def test_trigram_needs_three_chars(db, mem):
    """两字词命中不了 FTS，靠 LIKE 兜底——这是双通道存在的原因。"""
    mem.remember(group_msg("杭州天气不错啊"))

    hits = db.query(
        "SELECT rowid FROM episodes_fts WHERE episodes_fts MATCH ?", ['"杭州"']
    )

    assert hits == []
