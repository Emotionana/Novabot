"""文档知识库：切片、导入、检索、进 prompt。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.memory.knowledge import KnowledgeStore, chunk_text, keywords
from app.memory.system import MemorySystem
from app.models import ChatType, InboundMessage
from app.web.knowledge_api import router

DOC = """# 项目 Alpha

项目 Alpha 的截止日期是 12 月 15 日。
负责人是小林。

## 风险
- 鉴权还没做
- 文档导入刚接
"""


def test_chunk_text_keeps_line_numbers():
    text = "\n".join(f"第 {i} 行内容" for i in range(1, 201))
    chunks = chunk_text(text, size=200)

    assert len(chunks) > 1
    assert chunks[0][1] == 1
    assert chunks[-1][2] == 200
    assert all(len(body) <= 200 for body, _, _ in chunks)


def test_keywords_extracts_chinese_windows():
    terms = keywords("项目截止日期是什么")
    assert "项目截止日期是什么" in terms
    assert "项目" in terms


def test_import_search_and_context(db):
    store = KnowledgeStore(db)
    out = store.import_text(account_id="a", title="项目 Alpha", text=DOC, source="alpha.md")
    assert out["chunks"] >= 1

    hits = store.search(account_id="a", query="截止日期")
    assert hits and "12 月 15 日" in hits[0]["text"]

    line = store.context_line(account_id="a", query="项目截止日期", max_chars=300)
    assert "《项目 Alpha》" in line
    assert "来源" in line


def test_memory_context_includes_documents(db):
    mem = MemorySystem(db)
    mem.knowledge.import_text(account_id="a", title="项目 Alpha", text=DOC)
    mem.remember(
        InboundMessage(
            account_id="a", chat_id="private:111", chat_type=ChatType.PRIVATE,
            sender_qq="111", text="项目 Alpha 什么时候截止？",
        )
    )

    ctx = mem.build_context(
        chat_id="private:111", sender_qq="111", account_id="a", text="项目 Alpha 截止日期"
    )

    assert "12 月 15 日" in ctx
    assert "文档" in ctx


def test_knowledge_api_import_list_delete(db):
    app = FastAPI()
    app.state.db = db
    app.include_router(router)
    client = TestClient(app)

    r = client.post("/api/knowledge/import", json={
        "title": "Alpha", "text": DOC, "source": "alpha.md", "account_id": "a",
    }).json()
    assert r["ok"] and r["chunks"] >= 1

    docs = client.get("/api/knowledge/docs?account_id=a").json()["docs"]
    assert docs[0]["title"] == "Alpha"
    preview = client.get(f"/api/knowledge/docs/{r['id']}").json()
    assert preview["ok"] and "Alpha" in preview["doc"]["text"]

    assert client.delete(f"/api/knowledge/docs/{r['id']}").json()["ok"] is True
    assert client.get("/api/knowledge/docs?account_id=a").json()["docs"] == []
    assert client.get("/api/knowledge/search?q=截止日期").json()["hits"] == []


def test_upload_docx_and_txt(db):
    import io

    import docx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.state.db = db
    app.include_router(router)
    client = TestClient(app)

    d = docx.Document()
    d.add_heading("项目 Beta", level=1)
    d.add_paragraph("项目 Beta 的负责人是阿岚。")
    buf = io.BytesIO()
    d.save(buf)
    r = client.post(
        "/api/knowledge/import_file",
        files={"file": ("beta.docx", buf.getvalue(),
                         "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        data={"account_id": "a"},
    ).json()
    assert r["ok"], r
    doc = client.get("/api/knowledge/docs?account_id=a").json()["docs"][0]
    assert doc["title"] == "beta"
    preview = client.get(f"/api/knowledge/docs/{doc['id']}").json()
    assert "阿岚" in preview["doc"]["text"]

    r = client.post(
        "/api/knowledge/import_file",
        files={"file": ("note.txt", "普通文本内容".encode(), "text/plain")},
        data={"account_id": "a", "title": "手写标题"},
    ).json()
    assert r["ok"], r
