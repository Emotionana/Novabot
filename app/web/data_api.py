"""数据接口。

/api/data/*

记忆浏览、人物、事实、会话、统计。
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/data", tags=["data"])


class MemoryEditIn(BaseModel):
    text: str = ""
    name: str = ""
    account_id: str = ""


def _require_edit(request: Request) -> None:
    if str(request.headers.get("x-novabot-edit") or "") != "1":
        raise HTTPException(status_code=403, detail="编辑保险未打开")


def _owned_row(db, table: str, key_col: str, key, account_id: str):
    row = db.query_one(f"SELECT account_id FROM {table} WHERE {key_col} = ?", [key])
    if row is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if account_id and str(row["account_id"] or "") != account_id:
        raise HTTPException(status_code=404, detail="记录不属于当前账号")
    return row


def _soft_delete(db, table: str, key_col: str, key, account_id: str) -> dict:
    _owned_row(db, table, key_col, key, account_id)
    sql = f"UPDATE {table} SET gone = 1 WHERE {key_col} = ?"
    params: list = [key]
    if account_id:
        sql += " AND account_id = ?"
        params.append(account_id)
    db.execute(sql, params)
    return {"ok": True}



def _db(request: Request):
    return request.app.state.db


@router.get("/people")
def people(request: Request, limit: int = 200, q: str = "", account_id: str = "",
           sort: str = "recent"):
    db = _db(request)
    where = []
    params: list = []
    if account_id:
        where.append("account_id = ?")
        params.append(account_id)
    if q:
        where.append("(name LIKE ? OR alias LIKE ? OR qq LIKE ? OR EXISTS (SELECT 1 FROM person_group_names g WHERE g.qq = people.qq AND g.account_id = people.account_id AND g.card LIKE ?))")
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"])
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    order = {
        "recent": "last_seen DESC",
        "messages": "msg_count DESC, last_seen DESC",
        "name": "COALESCE(NULLIF(name, ''), qq) COLLATE NOCASE ASC, qq",
        "first": "first_seen ASC",
    }.get(sort, "last_seen DESC")
    lim = max(1, min(int(limit or 200), 500))
    total = int(db.scalar(f"SELECT COUNT(*) FROM people{clause}", params, default=0) or 0)
    rows = db.query(
        f"SELECT * FROM people{clause} ORDER BY {order} LIMIT ?",
        [*params, lim],
    )
    return {
        "ok": True,
        "total": total,
        "people": [
            {
                "qq": r["qq"],
                "account_id": r["account_id"],
                "name": r["name"] or "",
                "alias": r["alias"] or "",
                "style": r["style"] or "",
                "persona": r["persona"] or "",
                "handle": r["handle"] or "",
                "msg_count": r["msg_count"] or 0,
                "last_seen": r["last_seen"],
                "is_owner": bool(r["is_owner"]),
                "avatar": (
                    f"https://q1.qlogo.cn/g?b=qq&nk={r['qq']}&s=100"
                    if str(r["qq"]).isdigit() else ""
                ),
            }
            for r in rows
        ],
    }


@router.get("/person/{qq}")
def person_detail(request: Request, qq: str, account_id: str = ""):
    db = _db(request)
    row = db.query_one(
        "SELECT * FROM people WHERE qq = ? AND (account_id = ? OR ? = '')",
        [qq, account_id, account_id],
    )
    if row is None:
        return {"ok": False, "error": "没这个人"}

    # 细节页也要按账号隔离：同一个人在两个号下是两份画像
    fact_sql = "SELECT * FROM facts WHERE subject_id = ? AND gone = 0"
    ep_sql = (
        "SELECT id, text, event_at FROM episodes WHERE sender_qq = ? AND gone = 0"
    )
    emo_join = ""
    params_fact: list = [qq]
    params_ep: list = [qq]
    params_emo: list = [qq]
    if account_id:
        fact_sql += " AND account_id = ?"
        ep_sql += " AND account_id = ?"
        emo_join = (
            " JOIN episodes e ON e.id = emotions.episode_id"
            " WHERE emotions.sender_qq = ? AND e.account_id = ?"
        )
        params_fact.append(account_id)
        params_ep.append(account_id)
        params_emo.append(account_id)

    facts = db.query(fact_sql + " ORDER BY tier DESC, hits DESC LIMIT 50", params_fact)
    eps = db.query(ep_sql + " ORDER BY event_at DESC LIMIT 20", params_ep)
    if account_id:
        emos = db.query(
            "SELECT emotions.emotion, COUNT(*) AS n FROM emotions" + emo_join +
            " GROUP BY emotions.emotion ORDER BY n DESC",
            params_emo,
        )
    else:
        emos = db.query(
            "SELECT emotion, COUNT(*) AS n FROM emotions WHERE sender_qq = ? "
            "GROUP BY emotion ORDER BY n DESC",
            [qq],
        )

    group_rows = db.query(
        """SELECT g.group_id, g.card, g.first_seen, g.last_seen, c.title
           FROM person_group_names g
           LEFT JOIN chats c ON c.chat_id = 'group:' || g.group_id AND c.account_id = g.account_id
           WHERE g.qq = ? AND g.account_id = ?
           ORDER BY g.last_seen DESC LIMIT 50""",
        [qq, row["account_id"]],
    )
    return {
        "ok": True,
        "person": dict(row),
        "group_names": [
            {"group_id": g["group_id"], "card": g["card"],
             "title": g["title"] or "", "first_seen": g["first_seen"], "last_seen": g["last_seen"]}
            for g in group_rows
        ],
        "facts": [dict(f) for f in facts],
        "recent": [dict(e) for e in eps],
        "emotions": [{"emotion": e["emotion"], "count": e["n"]} for e in emos],
    }


@router.get("/episodes")
def episodes(request: Request, chat_id: str = "", limit: int = 50, offset: int = 0,
             account_id: str = "", q: str = ""):
    db = _db(request)
    where = ["gone = 0"]
    params: list = []
    if chat_id:
        where.append("chat_id = ?")
        params.append(chat_id)
    if account_id:
        where.append("account_id = ?")
        params.append(account_id)
    if q:
        where.append("(text LIKE ? OR sender_nick LIKE ? OR sender_qq LIKE ?)")
        params.extend([f"%{q}%"] * 3)
    clause = " WHERE " + " AND ".join(where)
    rows = db.query(
        f"SELECT * FROM episodes{clause} ORDER BY event_at DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    )
    return {
        "ok": True,
        "episodes": [
            {
                "id": r["id"],
                "chat_id": r["chat_id"],
                "sender": r["sender_nick"] or r["sender_qq"],
                "text": r["text"] or "",
                "at": r["event_at"],
                "distilled": bool(r["distilled"]),
            }
            for r in rows
        ],
    }


@router.get("/facts")
def facts(request: Request, subject: str = "", limit: int = 100, account_id: str = "", q: str = ""):
    db = _db(request)
    where = ["f.gone = 0"]
    params: list = []
    if subject:
        where.append("f.subject_id = ?")
        params.append(subject)
    if account_id:
        where.append("f.account_id = ?")
        params.append(account_id)
    if q:
        where.append("(f.text LIKE ? OR f.predicate LIKE ? OR f.object LIKE ? OR f.subject_id LIKE ?)")
        params.extend([f"%{q}%"] * 4)
    clause = " WHERE " + " AND ".join(where)
    rows = db.query(
        "SELECT f.*, p.name AS subject_name, p.alias AS subject_alias "
        "FROM facts f LEFT JOIN people p ON p.qq = f.subject_id AND p.account_id = f.account_id"
        f"{clause} ORDER BY f.tier DESC, f.recorded_at DESC LIMIT ?",
        [*params, limit],
    )
    return {"ok": True, "facts": [dict(r) for r in rows]}


@router.get("/gists")
def gists(request: Request, limit: int = 100, account_id: str = "", q: str = ""):
    db = _db(request)
    where = ["gone = 0"]
    params: list = []
    if account_id:
        where.append("account_id = ?")
        params.append(account_id)
    if q:
        where.append("text LIKE ?")
        params.append(f"%{q}%")
    rows = db.query(
        "SELECT id, account_id, level, chat_id, sender_qq, period_from, period_to, "
        "text, created_at FROM gists WHERE " + " AND ".join(where) +
        " ORDER BY period_to DESC LIMIT ?",
        [*params, max(1, min(limit, 300))],
    )
    return {"ok": True, "gists": [dict(r) for r in rows]}


@router.patch("/episodes/{episode_id}")
def edit_episode(episode_id: int, req: MemoryEditIn, request: Request):
    _require_edit(request)
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="内容不能为空")
    _owned_row(_db(request), "episodes", "id", episode_id, req.account_id)
    sql = "UPDATE episodes SET text = ? WHERE id = ?"
    params: list = [text[:8000], episode_id]
    if req.account_id:
        sql += " AND account_id = ?"
        params.append(req.account_id)
    _db(request).execute(sql, params)
    return {"ok": True, "id": episode_id}


@router.delete("/episodes/{episode_id}")
def delete_episode(episode_id: int, request: Request, account_id: str = ""):
    _require_edit(request)
    return _soft_delete(_db(request), "episodes", "id", episode_id, account_id)


@router.patch("/facts/{fact_id}")
def edit_fact(fact_id: int, req: MemoryEditIn, request: Request):
    _require_edit(request)
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="内容不能为空")
    _owned_row(_db(request), "facts", "id", fact_id, req.account_id)
    sql = "UPDATE facts SET text = ? WHERE id = ?"
    params: list = [text[:8000], fact_id]
    if req.account_id:
        sql += " AND account_id = ?"
        params.append(req.account_id)
    _db(request).execute(sql, params)
    return {"ok": True, "id": fact_id}


@router.delete("/facts/{fact_id}")
def delete_fact(fact_id: int, request: Request, account_id: str = ""):
    _require_edit(request)
    return _soft_delete(_db(request), "facts", "id", fact_id, account_id)


@router.patch("/gists/{gist_id}")
def edit_gist(gist_id: int, req: MemoryEditIn, request: Request):
    _require_edit(request)
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="内容不能为空")
    _owned_row(_db(request), "gists", "id", gist_id, req.account_id)
    sql = "UPDATE gists SET text = ? WHERE id = ?"
    params: list = [text[:8000], gist_id]
    if req.account_id:
        sql += " AND account_id = ?"
        params.append(req.account_id)
    _db(request).execute(sql, params)
    return {"ok": True, "id": gist_id}


@router.delete("/gists/{gist_id}")
def delete_gist(gist_id: int, request: Request, account_id: str = ""):
    _require_edit(request)
    return _soft_delete(_db(request), "gists", "id", gist_id, account_id)


def _count(db, table: str, account_id: str, where: str = "") -> int:
    sql = f"SELECT COUNT(*) FROM {table} WHERE 1=1"
    params: list = []
    if where:
        sql += " AND " + where
    if account_id:
        sql += " AND account_id = ?"
        params.append(account_id)
    return int(db.scalar(sql, params, default=0) or 0)


@router.get("/overview")
def overview(request: Request, account_id: str = ""):
    db = _db(request)
    counts = {
        "episodes": _count(db, "episodes", account_id, "gone = 0"),
        "facts": _count(db, "facts", account_id, "gone = 0"),
        "gists": _count(db, "gists", account_id, "gone = 0"),
        "people": _count(db, "people", account_id, "gone = 0"),
        "chats": _count(db, "chats", account_id),
        "documents": _count(db, "documents", account_id),
    }
    ep_where = "gone = 0" + (" AND account_id = ?" if account_id else "")
    ep_params = [account_id] if account_id else []
    eps = db.query(
        "SELECT id, sender_nick, sender_qq, text, chat_id, event_at, distilled "
        "FROM episodes WHERE " + ep_where + " ORDER BY event_at DESC LIMIT 6",
        ep_params,
    )
    facts = db.query(
        "SELECT f.id, f.subject_id, f.predicate, f.object, f.text, f.tier, f.recorded_at, "
        "p.name AS subject_name, p.alias AS subject_alias "
        "FROM facts f LEFT JOIN people p ON p.qq = f.subject_id AND p.account_id = f.account_id "
        "WHERE f.gone = 0" + (" AND f.account_id = ?" if account_id else "") +
        " ORDER BY f.tier DESC, f.recorded_at DESC LIMIT 6",
        ep_params,
    )
    gists = db.query(
        "SELECT id, level, text, period_to FROM gists WHERE gone = 0" +
        (" AND account_id = ?" if account_id else "") +
        " ORDER BY period_to DESC LIMIT 4",
        ep_params,
    )
    return {
        "ok": True,
        "counts": counts,
        "recent_episodes": [dict(r) for r in eps],
        "recent_facts": [dict(r) for r in facts],
        "recent_gists": [dict(r) for r in gists],
        "db_size": _db_size(db),
    }


@router.get("/search")
def search(request: Request, q: str, limit: int = 20, account_id: str = ""):
    q = (q or "").strip()
    if not q:
        return {"ok": True, "total": 0, "episodes": [], "facts": [],
                "gists": [], "people": [], "documents": []}
    db = _db(request)
    like = f"%{q}%"
    lim = max(1, min(limit, 50))
    acc = " AND account_id = ?" if account_id else ""
    ap = [account_id] if account_id else []

    episodes = db.query(
        "SELECT id, sender_nick, sender_qq, text, chat_id, event_at, distilled "
        "FROM episodes WHERE gone = 0" + acc +
        " AND (text LIKE ? OR sender_nick LIKE ? OR sender_qq LIKE ?) "
        "ORDER BY event_at DESC LIMIT ?",
        [*ap, like, like, like, lim],
    )
    facts = db.query(
        "SELECT f.id, f.subject_id, f.text, f.tier, f.recorded_at, "
        "p.name AS subject_name, p.alias AS subject_alias "
        "FROM facts f LEFT JOIN people p ON p.qq = f.subject_id AND p.account_id = f.account_id "
        "WHERE f.gone = 0" + (" AND f.account_id = ?" if account_id else "") +
        " AND (f.text LIKE ? OR f.predicate LIKE ? OR f.object LIKE ? OR f.subject_id LIKE ?) "
        "ORDER BY f.tier DESC, f.recorded_at DESC LIMIT ?",
        [*ap, like, like, like, like, lim],
    )
    gists = db.query(
        "SELECT id, level, text, period_to FROM gists WHERE gone = 0" + acc +
        " AND text LIKE ? ORDER BY period_to DESC LIMIT ?",
        [*ap, like, lim],
    )
    people = db.query(
        "SELECT qq, account_id, name, alias, style, msg_count, last_seen "
        "FROM people WHERE gone = 0" + acc +
        " AND (name LIKE ? OR alias LIKE ? OR qq LIKE ? OR handle LIKE ?) "
        "ORDER BY last_seen DESC LIMIT ?",
        [*ap, like, like, like, like, lim],
    )
    documents = db.query(
        "SELECT id, title, source, created_at FROM documents WHERE 1=1" + acc +
        " AND (title LIKE ? OR text LIKE ? OR source LIKE ?) ORDER BY created_at DESC LIMIT ?",
        [*ap, like, like, like, lim],
    )
    result = {
        "episodes": [dict(r) for r in episodes],
        "facts": [dict(r) for r in facts],
        "gists": [dict(r) for r in gists],
        "people": [dict(r) for r in people],
        "documents": [dict(r) for r in documents],
    }
    result["total"] = sum(len(v) for v in result.values())
    return {"ok": True, **result}


@router.get("/chats")
def chats(request: Request, limit: int = 50, account_id: str = ""):
    db = _db(request)
    if account_id:
        rows = db.query(
            "SELECT * FROM chats WHERE account_id = ? ORDER BY last_seen DESC LIMIT ?",
            [account_id, limit],
        )
    else:
        rows = db.query("SELECT * FROM chats ORDER BY last_seen DESC LIMIT ?", [limit])
    return {"ok": True, "chats": [dict(r) for r in rows]}


@router.get("/stats")
def stats(request: Request, account_id: str = ""):
    db = _db(request)
    from ..memory.migrate import table_counts

    counts = table_counts(db)
    today = time.time() - 86400
    if account_id:
        today_eps = db.scalar(
            "SELECT COUNT(*) FROM episodes WHERE event_at > ? AND account_id = ?",
            [today, account_id], default=0,
        )
    else:
        today_eps = db.scalar(
            "SELECT COUNT(*) FROM episodes WHERE event_at > ?", [today], default=0
        )
    return {
        "ok": True,
        "tables": counts,
        "today_episodes": today_eps,
        "db_size": _db_size(db),
    }


def _db_size(db) -> str:
    try:
        import os

        p = str(db.path)
        total = sum(
            os.path.getsize(p + s) for s in ("", "-wal", "-shm") if os.path.exists(p + s)
        )
        if total > 1024 * 1024:
            return f"{total / 1024 / 1024:.1f} MB"
        return f"{total / 1024:.0f} KB"
    except Exception:
        return "?"


@router.get("/tokens")
async def tokens(request: Request):
    """token 用量。"""
    meter = getattr(request.app.state, "meter", None)
    if meter is None:
        return {"ok": True, "summary": {}}
    return {"ok": True, "summary": meter.summary()}
