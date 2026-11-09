"""TokenMeter 跨重启恢复。"""

from __future__ import annotations

from app.core.tokens import CallRecord, TokenMeter


def test_snapshot_and_restore():
    m = TokenMeter()
    m.record(CallRecord(label="reply", n_in=10, n_out=5))

    snap = m.snapshot_today()
    n = TokenMeter()
    n.restore_today(snap)

    assert n.today()["calls"] == 1
    assert n.today()["total"] == 15


def test_restore_ignores_other_day():
    m = TokenMeter()
    m.record(CallRecord(label="reply", n_in=10, n_out=5))
    snap = m.snapshot_today()
    snap["day"] = "2000-01-01"

    n = TokenMeter()
    n.restore_today(snap)

    assert n.today().get("calls", 0) == 0
