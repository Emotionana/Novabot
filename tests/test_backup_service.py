"""备份包、暂存恢复与启动替换。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from app.backup_service import apply_pending_restore, create_backup, stage_restore


def test_backup_and_stage_restore(db, tmp_path):
    db.execute("CREATE TABLE IF NOT EXISTS backup_probe (v TEXT)")
    db.execute("INSERT INTO backup_probe(v) VALUES ('ok')")
    (tmp_path / "personas").mkdir()
    (tmp_path / "personas" / "a.txt").write_text("persona", encoding="utf-8")

    r = create_backup(db, data_dir=tmp_path)
    assert r["ok"] and Path(r["path"]).is_file()
    with zipfile.ZipFile(r["path"]) as z:
        assert "novabot-backup.json" in z.namelist()
        assert "data/novabot.db" in z.namelist()
        assert "data/personas/a.txt" in z.namelist()

    staged = stage_restore(Path(r["path"]), tmp_path)
    assert staged["ok"]
    assert (tmp_path / ".restore" / "personas" / "a.txt").is_file()
    assert apply_pending_restore(tmp_path) >= 2
    assert (tmp_path / "personas" / "a.txt").read_text(encoding="utf-8") == "persona"


def test_restore_rejects_path_traversal(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("novabot-backup.json", json.dumps({"app": "novabot", "schema": 1}))
        z.writestr("data/../evil.txt", "x")
    try:
        stage_restore(bad, tmp_path)
    except ValueError as e:
        assert "路径" in str(e) or "不允许" in str(e)
    else:
        raise AssertionError("应当拒绝路径穿越")
