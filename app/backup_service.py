"""一键备份 / 暂存恢复。

备份包含：数据库、人格、表情、权限、词典、模块数据、媒体、文件。
恢复不在运行中替换：先解到 data/.restore，下次启动读库前替换，并留一份
pre-restore 备份。这样不会把正在打开的 SQLite 写坏。
"""

from __future__ import annotations

import json
import shutil
import time
import zipfile
from pathlib import Path

APP = "novabot"
SCHEMA = 1
MANIFEST = "novabot-backup.json"
INCLUDE_DIRS = ("personas", "stickers", "permissions", "dict", "modules", "voice", "files", "media")
SKIP_NAMES = {"logs", "tmp", "backups", ".restore", "novabot.db-wal", "novabot.db-shm"}


def backup_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "backups"


def _skip(rel: Path) -> bool:
    return any(part in SKIP_NAMES for part in rel.parts)


def create_backup(db, *, data_dir: Path | None = None, note: str = "") -> dict:
    data_dir = Path(data_dir or Path(getattr(db, "path", "data/novabot.db")).parent)
    out_dir = backup_dir(data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"novabot-backup-{stamp}.zip"
    dest = out_dir / name
    tmp = dest.with_suffix(".zip.tmp")
    db_tmp = out_dir / f".{stamp}.db"
    try:
        db.backup(db_tmp)
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            z.write(db_tmp, "data/novabot.db")
            for dirname in INCLUDE_DIRS:
                root = data_dir / dirname
                if not root.is_dir():
                    continue
                for p in root.rglob("*"):
                    if not p.is_file() or _skip(p.relative_to(data_dir)):
                        continue
                    z.write(p, "data/" + p.relative_to(data_dir).as_posix())
            manifest = {
                "app": APP, "schema": SCHEMA, "created_at": time.time(),
                "note": str(note or "")[:200],
                "db": "data/novabot.db",
                "dirs": list(INCLUDE_DIRS),
            }
            z.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
        tmp.replace(dest)
        return {"ok": True, "name": name, "size": dest.stat().st_size, "path": str(dest)}
    finally:
        db_tmp.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)


def list_backups(data_dir: Path) -> list[dict]:
    out = []
    for p in sorted(backup_dir(data_dir).glob("novabot-backup-*.zip"), key=lambda x: x.stat().st_mtime, reverse=True):
        out.append({"name": p.name, "size": p.stat().st_size, "created_at": p.stat().st_mtime})
    return out


def backup_file(data_dir: Path, name: str) -> Path | None:
    safe = Path(str(name)).name
    if safe != name or not safe.startswith("novabot-backup-") or not safe.endswith(".zip"):
        return None
    p = backup_dir(data_dir) / safe
    return p if p.is_file() else None


def _safe_join(root: Path, rel: str) -> Path:
    target = (root / rel).resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        raise ValueError(f"不安全的路径：{rel}")
    return target


def stage_restore(zip_path: Path, data_dir: Path) -> dict:
    data_dir = Path(data_dir)
    stage = data_dir / ".restore"
    pending = data_dir / ".restore-pending.json"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            if MANIFEST not in names:
                raise ValueError("不是 Novabot 备份文件")
            manifest = json.loads(z.read(MANIFEST))
            if manifest.get("app") != APP or int(manifest.get("schema") or 0) != SCHEMA:
                raise ValueError("备份格式或版本不匹配")
            for name in names:
                if name == MANIFEST:
                    continue
                if not name.startswith("data/") or name.endswith("/"):
                    raise ValueError(f"备份里有不允许的路径：{name}")
                rel = name[5:]
                target = _safe_join(stage, rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(name) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
            if not (stage / "novabot.db").is_file():
                raise ValueError("备份里没有数据库")
        pending.write_text(json.dumps({
            "at": time.time(), "source": zip_path.name,
            "files": sum(1 for p in stage.rglob("*") if p.is_file()),
        }, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "staged": True, "files": sum(1 for p in stage.rglob("*") if p.is_file())}
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        pending.unlink(missing_ok=True)
        raise


def apply_pending_restore(data_dir: Path) -> int:
    """启动时、打开数据库之前调用。返回替换的文件数。"""
    data_dir = Path(data_dir)
    stage = data_dir / ".restore"
    pending = data_dir / ".restore-pending.json"
    if not pending.is_file() or not stage.is_dir():
        return 0
    keep = backup_dir(data_dir) / f"pre-restore-{time.strftime('%Y%m%d-%H%M%S')}"
    keep.mkdir(parents=True, exist_ok=True)
    count = 0
    for src in stage.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(stage)
        dst = data_dir / rel
        if dst.is_file():
            old = keep / rel
            old.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, old)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        count += 1
    for suffix in ("-wal", "-shm"):
        (data_dir / f"novabot.db{suffix}").unlink(missing_ok=True)
    shutil.rmtree(stage, ignore_errors=True)
    pending.unlink(missing_ok=True)
    return count
