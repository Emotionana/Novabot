"""文档知识库接口。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from pydantic import BaseModel

from ..memory.knowledge import KnowledgeStore

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class ImportIn(BaseModel):
    title: str = ""
    text: str
    source: str = ""
    account_id: str = ""


def _store(request: Request) -> KnowledgeStore:
    return KnowledgeStore(request.app.state.db)


@router.post("/import")
def import_doc(req: ImportIn, request: Request):
    try:
        result = _store(request).import_text(
            account_id=req.account_id,
            title=req.title,
            text=req.text,
            source=req.source,
        )
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, **result}


@router.post("/import_file")
async def import_file(
    request: Request,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form()] = "",
    source: Annotated[str, Form()] = "",
    account_id: Annotated[str, Form()] = "",
):
    """上传 md/txt/pdf/docx，抽取文本后走同一个知识库导入。"""
    name = file.filename or "upload.txt"
    suffix = Path(name).suffix.lower()
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        return {"ok": False, "error": "文件超过 8 MB"}

    try:
        if suffix in (".md", ".markdown", ".txt"):
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = data.decode("gb18030", errors="replace")
        elif suffix == ".pdf":
            import io

            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        elif suffix == ".docx":
            import io

            import docx

            doc = docx.Document(io.BytesIO(data))
            parts = [p.text for p in doc.paragraphs if p.text.strip()]
            for table in doc.tables:
                for row in table.rows:
                    parts.append("\t".join(c.text.strip() for c in row.cells))
            text = "\n".join(parts)
        else:
            return {"ok": False, "error": "只支持 md / txt / pdf / docx"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"解析失败：{e}"}

    if not (text or "").strip():
        return {"ok": False, "error": "没有抽到文字（扫描版 PDF 需要先 OCR）"}
    try:
        result = _store(request).import_text(
            account_id=account_id,
            title=title.strip() or Path(name).stem,
            text=text,
            source=source.strip() or name,
        )
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, **result}


@router.get("/docs/{doc_id}")
def get_doc(doc_id: int, request: Request):
    doc = _store(request).get_doc(doc_id)
    if doc is None:
        return {"ok": False, "error": "文档不存在"}
    return {"ok": True, "doc": doc}


@router.get("/docs")
def list_docs(request: Request, account_id: str = ""):
    return {"ok": True, "docs": _store(request).list_docs(account_id=account_id)}


@router.delete("/docs/{doc_id}")
def delete_doc(doc_id: int, request: Request):
    return {"ok": _store(request).delete_doc(doc_id)}


@router.get("/search")
def search(request: Request, q: str, account_id: str = ""):
    return {
        "ok": True,
        "hits": _store(request).search(account_id=account_id, query=q, limit=10),
    }
