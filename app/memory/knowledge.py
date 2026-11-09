"""文档知识库：导入、切片、按关键词召回。

最小版，不接 embedding：
- 按行/段落切 ~500 字，记录起止行号
- 检索用关键词 LIKE（中文取 2 字窗口），返回来源
- 召回进 prompt 时带《标题》和行号
"""

from __future__ import annotations

import logging
import re
import time

log = logging.getLogger(__name__)

MAX_DOC_CHARS = 2_000_000
CHUNK_CHARS = 500
MAX_CHUNKS = 200


def chunk_text(text: str, *, size: int = CHUNK_CHARS) -> list[tuple[str, int, int]]:
    """切成 (片段, 起始行, 结束行)。行号从 1 开始。"""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []

    lines = text.split("\n")
    out: list[tuple[str, int, int]] = []
    buf: list[str] = []
    start = 1
    length = 0

    def flush(end_line: int) -> None:
        nonlocal buf, start, length
        body = "\n".join(buf).strip()
        if body:
            out.append((body, start, end_line))
        buf = []
        length = 0

    for i, line in enumerate(lines, 1):
        # 超长单行：先冲掉缓冲，再按长度切
        if len(line) > size:
            flush(i - 1)
            for j in range(0, len(line), size):
                out.append((line[j:j + size], i, i))
            start = i + 1
            continue

        if length + len(line) + 1 > size and buf:
            flush(i - 1)
            start = i
        buf.append(line)
        length += len(line) + 1

    flush(len(lines))
    return out[:MAX_CHUNKS]


def keywords(query: str, limit: int = 10) -> list[str]:
    """从中英文里抠检索词。中文加 2 字窗口，避免整句匹配不到。"""
    q = (query or "").strip()
    terms: list[str] = []
    for m in re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", q):
        if m not in terms:
            terms.append(m)
        if re.search(r"[\u4e00-\u9fff]", m) and len(m) > 3:
            for i in range(0, min(len(m) - 1, 8), 2):
                part = m[i:i + 2]
                if part not in terms:
                    terms.append(part)
    return terms[:limit]


class KnowledgeStore:
    def __init__(self, db):
        self.db = db

    def import_text(
        self, *, account_id: str, title: str, text: str, source: str = ""
    ) -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("文档内容是空的")
        if len(text) > MAX_DOC_CHARS:
            raise ValueError(f"文档太大（上限 {MAX_DOC_CHARS // 1024} KB）")
        title = (title or "").strip() or "未命名文档"
        chunks = chunk_text(text)
        if not chunks:
            raise ValueError("文档没有可用的内容")

        now = time.time()
        doc_id = self.db.insert(
            "INSERT INTO documents (account_id, title, source, text, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            [account_id, title[:200], source[:300], text, now, now],
        )
        rows = [
            (doc_id, i, body, start, end, now)
            for i, (body, start, end) in enumerate(chunks)
        ]
        self.db.executemany(
            "INSERT INTO doc_chunks "
            "(document_id, chunk_index, text, line_start, line_end, created_at) "
            "VALUES (?,?,?,?,?,?)",
            rows,
        )
        log.info("文档已导入：%s（%d 片）", title, len(rows))
        return {"id": doc_id, "title": title, "chunks": len(rows)}

    def list_docs(self, *, account_id: str = "", limit: int = 50) -> list[dict]:
        sql = (
            "SELECT d.id, d.title, d.source, d.created_at, "
            "(SELECT COUNT(*) FROM doc_chunks c WHERE c.document_id = d.id) AS chunks "
            "FROM documents d"
        )
        params: list = []
        if account_id:
            sql += " WHERE d.account_id = ? OR d.account_id = ''"
            params.append(account_id)
        sql += " ORDER BY d.id DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.db.query(sql, params)]

    def get_doc(self, doc_id: int) -> dict | None:
        row = self.db.query_one(
            "SELECT id, account_id, title, source, text, created_at FROM documents WHERE id = ?",
            [doc_id],
        )
        return dict(row) if row else None

    def delete_doc(self, doc_id: int) -> bool:
        self.db.execute("DELETE FROM doc_chunks WHERE document_id = ?", [doc_id])
        n = self.db.execute("DELETE FROM documents WHERE id = ?", [doc_id])
        return n > 0

    def search(self, *, account_id: str, query: str, limit: int = 4) -> list[dict]:
        terms = keywords(query)
        if not terms:
            return []
        where = " OR ".join("c.text LIKE ?" for _ in terms)
        params: list = [f"%{t}%" for t in terms]
        sql = (
            "SELECT c.text, c.line_start, c.line_end, d.title, d.source "
            "FROM doc_chunks c JOIN documents d ON d.id = c.document_id "
            f"WHERE (d.account_id = ? OR d.account_id = '') AND ({where}) "
            "ORDER BY d.id DESC LIMIT ?"
        )
        params = [account_id, *params, limit]
        return [dict(r) for r in self.db.query(sql, params)]

    def context_line(
        self, *, account_id: str, query: str, max_chars: int = 240
    ) -> str:
        hits = self.search(account_id=account_id, query=query, limit=2)
        if not hits:
            return ""
        parts: list[str] = []
        used = 0
        for h in hits:
            text = str(h["text"]).replace("\n", " ").strip()
            source = f"（来源：{h['source'] or h['title']} 第 {h['line_start']}-{h['line_end']} 行）"
            keep = max(80, min(220, max_chars - 60))
            line = f"· 《{h['title']}》{text[:keep]}{source}"
            if used and used + len(line) > max_chars:
                break
            parts.append(line)
            used += len(line)
        return "\n".join(parts)
