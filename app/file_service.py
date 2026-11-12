"""文件传输：严格沙箱、权限、敏感内容扫描、发送审批。

文件只允许在 data/files/{inbox,outbox} 里，不接受任意主机路径。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
import uuid
from pathlib import Path

import httpx

from .models import ChatType, InboundMessage, MediaItem

log = logging.getLogger(__name__)

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|secret|password|passwd|token)\s*[=:]\s*[^\s]{8,}"),
)


def safe_name(name: str, fallback: str = "file") -> str:
    name = Path(str(name or "")).name
    name = re.sub(r"[^\w\u4e00-\u9fff.()\[\] -]+", "_", name).strip(" ._")
    return (name or fallback)[:120]


class FileService:
    def __init__(self, db, settings, account_id: str = "", *, media=None):
        self.db = db
        self.settings = settings
        self.media = media
        self.account_id = account_id or getattr(settings, "account_id", "") or "default"
        safe = re.sub(r"[^\w.-]+", "_", str(self.account_id))[:80] or "default"
        self.root = Path(getattr(db, "path", ".")).parent / "files" / safe
        self.inbox = self.root / "inbox"
        self.outbox = self.root / "outbox"
        self.pending_file = self.root / "pending.json"
        for d in (self.inbox, self.outbox):
            d.mkdir(parents=True, exist_ok=True)
        import contextlib

        with contextlib.suppress(Exception):
            self._warm_settings()

    def _warm_settings(self) -> None:
        self.settings.get("files.enabled")
        self.settings.get("files.receive_from")
        self.settings.get("files.send_to")
        self.settings.get("files.require_approval")
        self.settings.get("files.max_mb")
        self.settings.get("files.allowed_extensions")
        self.settings.get("files.blocked_names")
        self.settings.get("files.scan_secrets")

    def _get(self, key, default=None):
        try:
            value = self.settings.get(key)
            return default if value is None else value
        except KeyError:
            return default

    @property
    def enabled(self) -> bool:
        return bool(self._get("files.enabled", True))

    def _owner(self) -> str:
        return str(self._get("security.owner_qq", "") or "")

    def _id_set(self, key) -> set[str]:
        value = self._get(key, []) or []
        if isinstance(value, str):
            value = re.split(r"[,，;；\s]+", value)
        out = set()
        for x in value:
            text = str(x or "").strip()
            for p in ("private:", "group:", "qq:"):
                if text.startswith(p):
                    text = text[len(p):]
            if text:
                out.add(text)
        return out

    def _allowed_receive(self, msg: InboundMessage) -> bool:
        if not self.enabled:
            return False
        mode = str(self._get("files.receive_from", "owner"))
        owner = self._owner()
        if mode == "off":
            return False
        if mode == "owner":
            return bool(owner) and str(msg.sender_qq) == owner
        if mode == "all":
            return True
        if msg.chat_type == ChatType.GROUP:
            return str(msg.group_id or "") in self._id_set("security.group_whitelist")
        return str(msg.sender_qq) in self._id_set("security.private_whitelist")

    def _allowed_send(self, chat_id: str) -> bool:
        if not self.enabled:
            return False
        mode = str(self._get("files.send_to", "owner"))
        if mode == "off":
            return False
        if mode == "owner":
            return chat_id == f"private:{self._owner()}"
        if mode == "all":
            return True
        if chat_id.startswith("group:"):
            return chat_id[6:] in self._id_set("security.group_whitelist")
        if chat_id.startswith("private:"):
            return chat_id[8:] in self._id_set("security.private_whitelist")
        return False

    # ---------- 接收 ----------
    async def ingest(self, msg: InboundMessage) -> str:
        files = [item for item in (msg.media or []) if item.kind == "file"]
        if not self.enabled or not files:
            return ""
        if not self._allowed_receive(msg):
            return "[文件：当前权限不允许接收]"
        notes = []
        for item in files:
            name = safe_name(item.name or item.file or "file")
            try:
                data, mime = await self._download(item)
                self._check_name(name)
                self._check_size(len(data))
                target = self.inbox / (msg.account_id or "default") / f"{uuid.uuid4().hex[:8]}-{name}"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                notes.append(f"【文件】{name}（{self._human_size(len(data))}，已接收）")
                # 截图/图片文件也要读字；单靠 [文件] 标记模型看不到图。
                ext = Path(name).suffix.lower()
                if self.media is not None and ext in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
                    try:
                        mime2 = mime if str(mime or "").startswith("image/") else (f"image/{ext.lstrip('.')}" if ext != ".jpg" else "image/jpeg")
                        desc = await self.media.vision_text(
                            data, mime2,
                            "先逐字抄写图中所有可见文字，保留换行；再简短描述画面。没有文字就说没有文字。",
                            max_tokens=800,
                        )
                        if desc:
                            notes.append(f"【图片文字】{name}\n{desc}")
                    except Exception as e:  # noqa: BLE001
                        log.warning("图片文件识别失败：%s", e or type(e).__name__)
                self._audit(msg, "receive", name, True, "")
            except Exception as e:  # noqa: BLE001
                reason = str(e) or type(e).__name__
                log.warning("文件接收失败：%s", reason)
                notes.append(f"【文件】{name}（未接收：{reason[:100]}）")
                self._audit(msg, "receive", name, False, str(e))
        return "\n".join(notes)

    async def _download(self, item: MediaItem) -> tuple[bytes, str]:
        limit = int(self._get("files.max_mb", 20) or 20) * 1024 * 1024
        if item.url.startswith("base64://"):
            data = base64.b64decode(item.url[9:])
            self._check_size(len(data))
            return data[:limit], item.mime
        if not item.url.startswith(("http://", "https://")):
            raise RuntimeError("没有可下载的文件地址")
        last = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c, \
                        c.stream("GET", item.url, headers={"User-Agent": "Novabot/0.1"}) as r:
                    r.raise_for_status()
                    chunks = bytearray()
                    async for chunk in r.aiter_bytes(65536):
                        chunks.extend(chunk)
                        if len(chunks) > limit:
                            raise RuntimeError("文件超过大小上限")
                    return bytes(chunks), r.headers.get("content-type", "")
            except Exception as e:  # noqa: BLE001
                last = e
                if attempt == 0:
                    await asyncio.sleep(0.5)
        raise RuntimeError(str(last) or type(last).__name__)

    # ---------- 发送 ----------
    def upload(self, filename: str, data: bytes) -> dict:
        if not self.enabled:
            return {"ok": False, "error": "文件模块没启用"}
        name = safe_name(filename)
        try:
            self._check_name(name)
            self._check_size(len(data))
            if self._get("files.scan_secrets", True):
                self._scan_secrets(name, data)
            target = self.outbox / name
            target.write_bytes(data)
            return {"ok": True, "name": name, "size": len(data)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:200]}

    def request(self, name: str, *, chat_id: str, account_id: str, sender: str = "") -> dict:
        if not self.enabled:
            return {"ok": False, "error": "文件模块没启用"}
        if not self._allowed_send(chat_id):
            return {"ok": False, "error": "当前目标不允许发文件"}
        try:
            path = self._check_outbox(name)
            record = {
                "id": uuid.uuid4().hex[:10],
                "name": path.name,
                "path": str(path),
                "chat_id": chat_id,
                "account_id": account_id,
                "sender": sender,
                "created_at": time.time(),
                "status": "pending",
            }
            rows = self._pending()
            rows = [r for r in rows if r.get("status") == "pending"][-49:]
            rows.append(record)
            self._save_pending(rows)
            requires = bool(self._get("files.require_approval", True))
            return {
                "ok": True, "id": record["id"], "name": path.name, "path": str(path),
                "requires_approval": requires,
                "message": (f"已提交文件审批：{path.name}（{record['id']}）"
                            if requires else f"已加入发送队列：{path.name}"),
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:200]}

    def resolve_sendable(self, value: str, *, account_id: str = "") -> Path:
        """把标记里的文件解析成可发送路径。

        允许：账号 outbox、共享 outbox、当前账号 workspace。
        其他主机路径一律拒绝。
        """
        raw = Path(str(value or "").strip())
        if not str(raw):
            raise ValueError("文件路径为空")
        shared = self.root.parent / "outbox"
        ws = Path(getattr(self.db, "path", ".")).parent.parent / "workspaces" / (account_id or self.account_id)
        roots = [self.outbox.resolve(), shared.resolve(), ws.resolve()]
        if raw.is_absolute():
            candidate = raw
        else:
            candidate = next((root / raw for root in roots if (root / raw).is_file()), raw)
        candidate = candidate.resolve()
        if not any(candidate == root or root in candidate.parents for root in roots):
            raise ValueError("只能发送 outbox 或当前账号 workspace 里的文件")
        if not candidate.is_file():
            raise ValueError("文件不存在")
        self._check_name(candidate.name)
        self._check_size(candidate.stat().st_size)
        if self._get("files.scan_secrets", True):
            self._scan_secrets(candidate.name, candidate.read_bytes()[:2 * 1024 * 1024])
        return candidate

    def _check_outbox(self, name: str) -> Path:
        if not name:
            raise ValueError("文件名为空")
        candidate = (self.outbox / safe_name(name)).resolve()
        root = self.outbox.resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError("只能发送 data/files/outbox 里的文件")
        if not candidate.is_file():
            raise ValueError("出站箱里没有这个文件")
        self._check_name(candidate.name)
        self._check_size(candidate.stat().st_size)
        if self._get("files.scan_secrets", True):
            self._scan_secrets(candidate.name, candidate.read_bytes()[:2 * 1024 * 1024])
        return candidate

    def _check_name(self, name: str) -> None:
        allowed = {str(x).lower().lstrip(".") for x in (self._get("files.allowed_extensions", []) or [])}
        ext = Path(name).suffix.lower().lstrip(".")
        if allowed and ext not in allowed:
            raise ValueError(f"不允许的文件类型：.{ext or '无扩展名'}")
        low = name.casefold()
        for bad in (self._get("files.blocked_names", []) or []):
            if str(bad).casefold() in low:
                raise ValueError("文件名命中禁止项")

    def _check_size(self, size: int) -> None:
        limit = int(self._get("files.max_mb", 20) or 20) * 1024 * 1024
        if size > limit:
            raise ValueError(f"文件超过 {limit // 1024 // 1024} MB")

    @staticmethod
    def _scan_secrets(name: str, data: bytes) -> None:
        if b"\x00" in data[:4096]:
            return
        text = data[:1024 * 1024].decode("utf-8", errors="ignore")
        if not text:
            return
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                raise ValueError("文件里检测到密钥/密码，拒绝发送")
        name_low = name.casefold()
        if any(x in name_low for x in (".env", "id_rsa", "id_ed25519", "password", "cookie", "token", "wallet")):
            raise ValueError("文件名像敏感文件，拒绝发送")

    @staticmethod
    def _human_size(n: int) -> str:
        if n >= 1024 * 1024:
            return f"{n / 1024 / 1024:.1f} MB"
        if n >= 1024:
            return f"{n / 1024:.0f} KB"
        return f"{n} B"

    def _pending(self) -> list[dict]:
        try:
            return json.loads(self.pending_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return []

    def _save_pending(self, rows: list[dict]) -> None:
        tmp = self.pending_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows[-50:], ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.pending_file)

    def pending(self) -> list[dict]:
        return [r for r in self._pending() if r.get("status") == "pending"]

    def reject(self, req_id: str) -> dict:
        rows = self._pending()
        for r in rows:
            if r.get("id") == req_id:
                r["status"] = "rejected"
                self._save_pending(rows)
                return {"ok": True}
        return {"ok": False, "error": "没有这个审批"}

    def approve(self, req_id: str) -> dict:
        rows = self._pending()
        for r in rows:
            if r.get("id") != req_id or r.get("status") != "pending":
                continue
            try:
                path = self._check_outbox(r["name"])
            except Exception as e:  # noqa: BLE001
                r["status"] = "rejected"
                self._save_pending(rows)
                return {"ok": False, "error": str(e)[:200]}
            r["status"] = "approved"
            r["approved_at"] = time.time()
            self._save_pending(rows)
            return {"ok": True, "path": str(path), "chat_id": r["chat_id"], "account_id": r["account_id"], "name": path.name}
        return {"ok": False, "error": "没有待审批的文件"}

    def mark_sent(self, req_id: str) -> None:
        rows = self._pending()
        for r in rows:
            if r.get("id") == req_id:
                r["status"] = "sent"
                r["sent_at"] = time.time()
        self._save_pending(rows)

    def list(self, box: str = "outbox") -> list[dict]:
        root = self.outbox if box != "inbox" else self.inbox
        out = []
        for p in sorted(root.rglob("*"), key=lambda x: x.stat().st_mtime if x.is_file() else 0, reverse=True)[:100]:
            if p.is_file():
                out.append({"name": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime})
        return out

    def status(self) -> dict:
        return {
            "ok": True, "enabled": self.enabled,
            "receive_from": self._get("files.receive_from", "owner"),
            "send_to": self._get("files.send_to", "owner"),
            "require_approval": bool(self._get("files.require_approval", True)),
            "max_mb": int(self._get("files.max_mb", 20) or 20),
            "pending": len(self.pending()),
            "outbox": len(self.list("outbox")),
            "inbox": len(self.list("inbox")),
        }

    def _audit(self, msg: InboundMessage, action: str, name: str, ok: bool, detail: str) -> None:
        log.info("文件%s：%s / %s / %s / %s", action, msg.account_id, msg.chat_id, name, "ok" if ok else detail[:80])
