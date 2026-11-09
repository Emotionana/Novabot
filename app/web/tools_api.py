"""工具/笔记/提醒的只读接口（给网页和排查用）。"""

from __future__ import annotations

from datetime import datetime
from time import time
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..settings import Settings
from ..tools.when import format_when, parse_when

router = APIRouter(prefix="/api/tools", tags=["tools"])


class NoteIn(BaseModel):
    title: str = ""
    body: str
    account_id: str = ""


class ReminderIn(BaseModel):
    text: str
    when: str
    account_id: str = ""


class ReminderPatch(BaseModel):
    text: str | None = None
    when: str | None = None


def _owner(request: Request) -> tuple[str, str]:
    """返回 (owner_qq, account_id)。笔记/提醒必须挂在主人名下。"""
    db = request.app.state.db
    try:
        owner = str(Settings(db).get("security.owner_qq") or "")
    except Exception:  # noqa: BLE001
        owner = ""
    return owner, str(request.query_params.get("account_id") or "")


def _tz(db):
    try:
        name = Settings(db).get("server.timezone") or "Asia/Shanghai"
        return ZoneInfo(str(name))
    except Exception:  # noqa: BLE001
        return datetime.now().astimezone().tzinfo


@router.get("/notes")
def notes(request: Request, limit: int = 20, account_id: str = ""):
    db = request.app.state.db
    sql = "SELECT id, account_id, person_qq, title, body, created_at FROM notes"
    params: list = []
    if account_id:
        sql += " WHERE account_id = ?"
        params.append(account_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = db.query(sql, params)
    return {"ok": True, "notes": [dict(r) for r in rows]}


@router.get("/reminders")
def reminders(request: Request, limit: int = 20, account_id: str = ""):
    db = request.app.state.db
    sql = "SELECT * FROM reminders"
    params: list = []
    if account_id:
        sql += " WHERE account_id = ?"
        params.append(account_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = db.query(sql, params)
    return {"ok": True, "reminders": [dict(r) for r in rows]}


class NotePatch(BaseModel):
    title: str | None = None
    body: str | None = None


@router.patch("/notes/{note_id}")
def edit_note(note_id: int, req: NotePatch, request: Request):
    db = request.app.state.db
    row = db.query_one("SELECT * FROM notes WHERE id = ?", [note_id])
    if row is None:
        return {"ok": False, "error": "笔记不存在"}
    title = req.title if req.title is not None else (row["title"] or "")
    body = req.body if req.body is not None else (row["body"] or "")
    if not body.strip():
        return {"ok": False, "error": "内容不能为空"}
    db.execute(
        "UPDATE notes SET title = ?, body = ?, updated_at = ? WHERE id = ?",
        [title.strip() or body.strip()[:20], body.strip()[:5000], time(), note_id],
    )
    return {"ok": True}


@router.post("/notes")
def add_note(req: NoteIn, request: Request):
    body = (req.body or "").strip()
    if not body:
        return {"ok": False, "error": "内容不能为空"}
    owner, _ = _owner(request)
    if not owner:
        return {"ok": False, "error": "先在「设置 → 安全」里填主人 QQ"}
    db = request.app.state.db
    now = time()
    note_id = db.insert(
        "INSERT INTO notes (account_id, person_qq, chat_id, title, body, tags, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [
            req.account_id,
            owner,
            f"private:{owner}",
            (req.title or "").strip() or body[:20],
            body[:5000],
            "[]",
            now,
            now,
        ],
    )
    return {"ok": True, "id": note_id}


@router.delete("/notes/{note_id}")
def delete_note(note_id: int, request: Request):
    db = request.app.state.db
    n = db.execute("DELETE FROM notes WHERE id = ?", [note_id])
    return {"ok": n > 0}


@router.post("/reminders")
def add_reminder(req: ReminderIn, request: Request):
    text = (req.text or "").strip()
    if not text:
        return {"ok": False, "error": "提醒内容不能为空"}
    owner, _ = _owner(request)
    if not owner:
        return {"ok": False, "error": "先在「设置 → 安全」里填主人 QQ"}
    db = request.app.state.db
    tz = _tz(db)
    try:
        due = parse_when(req.when, now=time(), tz=tz)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    now = time()
    reminder_id = db.insert(
        "INSERT INTO reminders "
        "(account_id, person_qq, chat_id, text, due_at, status, attempts, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [req.account_id, owner, f"private:{owner}", text[:500], due, "pending", 0, now],
    )
    return {"ok": True, "id": reminder_id, "due": format_when(due, tz)}


@router.patch("/reminders/{reminder_id}")
def edit_reminder(reminder_id: int, req: ReminderPatch, request: Request):
    db = request.app.state.db
    row = db.query_one("SELECT * FROM reminders WHERE id = ?", [reminder_id])
    if row is None:
        return {"ok": False, "error": "提醒不存在"}
    if row["status"] != "pending":
        return {"ok": False, "error": "这条提醒已经结束，不能再改"}
    text = (req.text if req.text is not None else row["text"] or "").strip()
    if not text:
        return {"ok": False, "error": "提醒内容不能为空"}
    tz = _tz(db)
    due = row["due_at"]
    if req.when:
        try:
            due = parse_when(req.when, now=time(), tz=tz)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
    db.execute(
        "UPDATE reminders SET text = ?, due_at = ?, attempts = 0 WHERE id = ?",
        [text[:500], due, reminder_id],
    )
    return {"ok": True, "due": format_when(due, tz)}


@router.delete("/reminders/{reminder_id}")
def cancel_reminder(reminder_id: int, request: Request):
    db = request.app.state.db
    n = db.execute(
        "UPDATE reminders SET status='cancelled' WHERE id = ? AND status = 'pending'",
        [reminder_id],
    )
    return {"ok": n > 0}


@router.get("/calls")
def calls(request: Request, limit: int = 20, account_id: str = ""):
    db = request.app.state.db
    sql = "SELECT * FROM tool_calls"
    params: list = []
    if account_id:
        sql += " WHERE account_id = ?"
        params.append(account_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = db.query(sql, params)
    return {"ok": True, "calls": [dict(r) for r in rows]}
