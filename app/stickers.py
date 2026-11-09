"""表情包：每个账号独立库、收集、检索、发送和自动配图。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import random
import re
import time
from pathlib import Path

import httpx

from .models import ChatType, InboundMessage

log = logging.getLogger(__name__)

SCOPES = ("owner_only", "private_safe", "group_safe", "shared")
SCOPE_LABELS = {
    "owner_only": "只主人私聊",
    "private_safe": "私聊可用",
    "group_safe": "群聊/私聊可用",
    "shared": "全部可用",
}
NEVER_RANDOM = "不随机"

EMOTIONS = {
    "开心": ("开心", "高兴", "笑", "哈哈", "好耶", "棒", "喜欢", "可爱"),
    "无语": ("无语", "沉默", "服了", "离谱", "尴尬"),
    "生气": ("生气", "气死", "恼火", "怒", "讨厌"),
    "委屈": ("委屈", "难过", "伤心", "哭", "不开心"),
    "疑惑": ("疑惑", "问号", "不懂", "困惑", "奇怪"),
    "害羞": ("害羞", "脸红", "不好意思", "亲亲", "贴贴"),
    "困": ("困", "累", "睡觉", "晚安", "疲惫"),
    "嘲讽": ("嘲讽", "阴阳", "讽刺", "就这", "呵呵"),
}


def sniff_image(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def image_size(data: bytes) -> tuple[int, int]:
    try:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        if data.startswith(b"GIF8"):
            return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
        if data.startswith(b"\xff\xd8\xff"):
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
                length = int.from_bytes(data[i + 2:i + 4], "big")
                i += 2 + max(2, length)
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            chunk = data[12:16]
            if chunk == b"VP8X" and len(data) >= 30:
                return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
            if chunk == b"VP8 " and len(data) >= 30:
                return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
    except Exception:  # noqa: BLE001
        pass
    return 0, 0


def safe_account(value: str) -> str:
    text = re.sub(r"[^\w.-]+", "_", str(value or "default"))
    return text[:80] or "default"


class StickerService:
    def __init__(self, db, settings, account_id: str = "", llm=None):
        self.db = db
        self.settings = settings
        self.account_id = account_id or getattr(settings, "account_id", "") or "default"
        self.llm = llm
        self.root = Path(getattr(db, "path", ".")).parent / "stickers" / safe_account(self.account_id)
        self.library_file = self.root / "library.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self._cooldown: dict[str, float] = {}
        self._last_md5: dict[str, str] = {}
        self.settings.get("stickers.enabled")
        self.settings.get("stickers.collect")
        self.settings.get("stickers.auto_send")
        self.settings.get("stickers.auto_private")
        self.settings.get("stickers.auto_group")
        self.settings.get("stickers.cooldown_sec")
        self.settings.get("stickers.collect_per_hour")
        self.settings.get("stickers.max_mb")
        self.settings.get("stickers.max_items")

    def _get(self, key, default=None):
        try:
            value = self.settings.get(key)
            return default if value is None else value
        except KeyError:
            return default

    def _load(self) -> dict:
        try:
            data = json.loads(self.library_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) and isinstance(data.get("items"), list) else {"version": 1, "items": []}
        except Exception:  # noqa: BLE001
            return {"version": 1, "items": []}

    def _save(self, data: dict) -> None:
        tmp = self.library_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.library_file)

    def status(self) -> dict:
        items = self._load()["items"]
        return {
            "ok": True, "account_id": self.account_id,
            "count": len(items), "enabled": bool(self._get("stickers.enabled", True)),
            "collect": bool(self._get("stickers.collect", False)),
            "auto_send": bool(self._get("stickers.auto_send", True)),
            "auto_private": int(self._get("stickers.auto_private", 6) or 0),
            "auto_group": int(self._get("stickers.auto_group", 12) or 0),
            "cooldown_sec": int(self._get("stickers.cooldown_sec", 0) or 0),
            "scopes": SCOPE_LABELS,
        }

    def list(self, query: str = "", scope: str = "") -> list[dict]:
        q = str(query or "").strip()
        items = self._load()["items"]
        out = []
        for it in items:
            if scope and it.get("scope") != scope:
                continue
            if q and not (
                q in str(it.get("name") or "")
                or any(q in str(t) for t in (it.get("tags") or []))
            ):
                continue
            out.append(dict(it, url="/api/stickers/image/" + str(it.get("md5") or "")))
        return sorted(out, key=lambda x: x.get("created_at", 0), reverse=True)

    def image(self, md5: str) -> Path | None:
        for it in self._load()["items"]:
            if it.get("md5") == md5:
                p = self.root / str(it.get("file") or "")
                return p if p.is_file() and self.root in p.resolve().parents else None
        return None

    def add_bytes(self, data: bytes, *, name: str = "", tags: list[str] | None = None,
                  source: str = "manual", scope: str = "owner_only",
                  origin_chat: str = "") -> dict:
        if not data:
            return {"ok": False, "error": "空图片"}
        limit = int(self._get("stickers.max_mb", 5) or 5) * 1024 * 1024
        if len(data) > limit:
            return {"ok": False, "error": f"图片超过 {limit // 1024 // 1024} MB"}
        mime = sniff_image(data)
        if not mime:
            return {"ok": False, "error": "只支持 jpg/png/gif/webp"}
        width, height = image_size(data)
        if width and height and (min(width, height) < 40 or max(width, height) > 4096):
            return {"ok": False, "error": "尺寸不像表情包"}
        md5 = hashlib.md5(data).hexdigest()
        lib = self._load()
        for it in lib["items"]:
            if it.get("md5") == md5:
                return {"ok": True, "item": it, "existed": True}
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp"}[mime]
        file = f"{md5}.{ext}"
        (self.root / file).write_bytes(data)
        item = {
            "md5": md5, "file": file, "name": str(name or "")[:12],
            "tags": [str(t)[:8] for t in (tags or []) if str(t).strip()][:5],
            "source": source, "origin_chat": origin_chat,
            "scope": scope if scope in SCOPES else "owner_only",
            "width": width, "height": height, "bytes": len(data), "mime": mime,
            "enabled": True, "uses": 0, "last_used": 0, "created_at": time.time(),
            "description": "", "text": "", "emotion": "", "meaning": "",
            "use_when": "", "avoid_when": "", "analyzed": False,
            "analysis_error": "", "analysis_at": 0,
            "emoji_id": "", "package_id": 0, "market_key": "",
        }
        lib["items"].append(item)
        max_items = int(self._get("stickers.max_items", 1000) or 1000)
        if len(lib["items"]) > max_items:
            lib["items"].sort(key=lambda x: (x.get("uses", 0), x.get("last_used", 0)))
            lib["items"] = lib["items"][-max_items:]
        self._save(lib)
        return {"ok": True, "item": item}

    async def collect(self, msg: InboundMessage, *, allowed: bool) -> int:
        if not allowed or not self._get("stickers.enabled", True) or not self._get("stickers.collect", False):
            return 0
        lib = self._load()
        now = time.time()
        recent = sum(1 for it in lib["items"] if it.get("source") == "chat" and now - float(it.get("created_at") or 0) < 3600)
        if recent >= int(self._get("stickers.collect_per_hour", 10) or 10):
            return 0
        collected = 0
        for item in msg.media:
            if item.kind not in ("sticker",):
                continue
            try:
                data = await self._download(item)
                scope = "owner_only" if (msg.chat_type == ChatType.PRIVATE and msg.sender_qq == str(self._get("security.owner_qq", ""))) else ("group_safe" if msg.chat_type == ChatType.GROUP else "private_safe")
                r = self.add_bytes(data, source="chat", scope=scope, origin_chat=msg.chat_id)
                if r.get("ok") and not r.get("existed"):
                    collected += 1
                    asyncio.create_task(self.analyze(r["item"]["md5"], self.llm))
            except Exception as e:  # noqa: BLE001
                log.debug("收表情失败：%s", e)
        return collected

    async def _download(self, item) -> bytes:
        if item.url.startswith("base64://"):
            return base64.b64decode(item.url[9:])
        if not item.url.startswith(("http://", "https://")):
            raise RuntimeError("没有可下载的表情地址")
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
            r = await c.get(item.url, headers={"User-Agent": "Novabot/0.1"})
            r.raise_for_status()
            return r.content

    def scope_ok(self, item: dict, msg: InboundMessage, *, owner_qq: str = "") -> bool:
        scope = item.get("scope") or "owner_only"
        if scope == "shared":
            return True
        if scope == "group_safe":
            return True
        if scope == "private_safe":
            return msg.chat_type == ChatType.PRIVATE
        return msg.chat_type == ChatType.PRIVATE and str(msg.sender_qq) == str(owner_qq)

    def _terms(self, item: dict) -> list[str]:
        values = [
            item.get("name"), item.get("emotion"), item.get("meaning"),
            item.get("use_when"), item.get("description"), item.get("text"),
            *(item.get("tags") or []),
        ]
        return [str(x).strip() for x in values if str(x or "").strip()]

    def _score(self, query: str, item: dict) -> float:
        q = re.sub(r"[\s，。！？、,.!?~～]+", "", str(query or ""))
        if not q:
            return 0
        best = 0.0
        for hay in self._terms(item):
            h = re.sub(r"[\s，。！？、,.!?~～]+", "", hay)
            if not h:
                continue
            if h == q:
                best = max(best, 12)
            elif h in q or q in h:
                best = max(best, 7)
            else:
                common = len(set(q) & set(h))
                if common >= 2:
                    best = max(best, common / max(len(q), 1) * 5)
        # 情绪同义词：开心/高兴/笑 都算一类
        for emotion, words in EMOTIONS.items():
            if any(w in q for w in words):
                hay = " ".join(self._terms(item))
                if emotion in hay or any(w in hay for w in words):
                    best = max(best, 6)
        return best

    def pick(self, query: str, msg: InboundMessage, *, owner_qq: str = "", exclude_md5: str = "") -> dict | None:
        """按语义选；有多个贴合项时在靠前的候选里换一张，避免总发同一张。"""
        items = [it for it in self._load()["items"] if it.get("enabled", True) and self.scope_ok(it, msg, owner_qq=owner_qq)]
        if not items:
            return None
        q = str(query or "").strip()
        if not q:
            return None
        scored = sorted(((self._score(q, it), it) for it in items), key=lambda x: x[0], reverse=True)
        fit = [it for s, it in scored if s >= 2.5]
        if not fit:
            return None
        if exclude_md5:
            alternatives = [it for it in fit if it.get("md5") != exclude_md5]
            if alternatives:
                fit = alternatives
        return random.choice(fit[:3])

    def auto_pick(self, text: str, msg: InboundMessage, *, owner_qq: str = "") -> dict | None:
        if not self._get("stickers.enabled", True) or not self._get("stickers.auto_send", True):
            return None
        key = msg.chat_id
        cooldown = float(self._get("stickers.cooldown_sec", 0) or 0)
        if cooldown > 0 and time.time() - self._cooldown.get(key, 0) < cooldown:
            return None
        chance = int(self._get("stickers.auto_group", 12) if msg.chat_type == ChatType.GROUP else self._get("stickers.auto_private", 6))
        if chance <= 0 or random.randrange(100) >= chance:
            return None
        last = self._last_md5.get(key, "")
        pick = self.pick(text, msg, owner_qq=owner_qq, exclude_md5=last)
        if pick is None:
            # 语义匹配不到时，从可用库里随机补一张，但尽量避开上一张。
            items = [
                it for it in self._load()["items"]
                if it.get("enabled", True) and self.scope_ok(it, msg, owner_qq=owner_qq)
                and it.get("md5") != last
            ]
            if not items:
                items = [
                    it for it in self._load()["items"]
                    if it.get("enabled", True) and self.scope_ok(it, msg, owner_qq=owner_qq)
                ]
            if items:
                pick = random.choice(items)
        if pick:
            self._cooldown[key] = time.time()
            self._last_md5[key] = str(pick.get("md5") or "")
        return pick

    async def analyze(self, md5: str, llm=None) -> dict:
        """用视觉模型理解表情：画面、文字、情绪、含义、适用/不适用场景。"""
        lib = self._load()
        item = next((x for x in lib["items"] if x.get("md5") == md5), None)
        if not item:
            return {"ok": False, "error": "没有这张表情"}
        path = self.root / str(item.get("file") or "")
        if not path.is_file():
            return {"ok": False, "error": "表情文件不存在"}
        from .media_service import MediaService

        prompt = (
            "这是 QQ 聊天里的一个表情包。请认真理解它，不要只描述颜色。"
            "只输出一个 JSON，不要 Markdown："
            '{"description":"画面里是谁/什么，包含哪些动作","text":"图中文字，没有就空",'
            '"emotion":"最主要情绪，例如开心/无语/生气/委屈/疑惑/害羞/困/嘲讽",'
            '"meaning":"它在这个语境里表达什么意思","use_when":"适合在什么聊天场景发",'
            '"avoid_when":"什么场景不适合发"}'
        )
        known = []
        if str(item.get("name") or "").strip():
            known.append(f"名称：{item['name']}")
        if item.get("tags"):
            known.append("标签：" + "、".join(str(x) for x in item["tags"][:8]))
        if str(item.get("text") or "").strip():
            known.append(f"图中文字：{item['text']}")
        if str(item.get("meaning") or "").strip():
            known.append(f"已有含义（仅供参考）：{item['meaning']}")
        if known:
            prompt += "已有信息：" + "；".join(known) + "。请以图片为准重新判断。"
        try:
            raw = await MediaService(self.db, self.settings, llm or self.llm).vision_text(
                path.read_bytes(), str(item.get("mime") or ""), prompt, max_tokens=500,
            )
        except Exception as e:  # noqa: BLE001
            raw = f"分析失败：{e}"
        if raw.startswith(("未配置视觉模型", "视觉接口 HTTP", "视觉识别失败", "视觉读取已关闭", "分析失败")):
            self._set_analysis(md5, {"analysis_error": raw, "analyzed": False})
            return {"ok": False, "error": raw}
        try:
            m = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(m.group(0) if m else raw)
        except Exception:  # noqa: BLE001
            data = {"description": raw[:500], "meaning": raw[:300]}
        update = {
            "description": str(data.get("description") or "")[:500],
            "text": str(data.get("text") or "")[:100],
            "emotion": str(data.get("emotion") or "")[:20],
            "meaning": str(data.get("meaning") or "")[:300],
            "use_when": str(data.get("use_when") or "")[:200],
            "avoid_when": str(data.get("avoid_when") or "")[:200],
            "analyzed": True, "analysis_error": "", "analysis_at": time.time(),
        }
        # 情绪和模型给的短标签一起进标签，方便自然语言检索
        tags = [str(x)[:8] for x in (item.get("tags") or []) if str(x).strip()]
        if update["emotion"] and update["emotion"] not in tags:
            tags.append(update["emotion"])
        for x in (data.get("tags") or []):
            tag = str(x).strip()[:8]
            if tag and tag not in tags:
                tags.append(tag)
        if tags:
            update["tags"] = tags[:8]
        self._set_analysis(md5, update)
        return {"ok": True, "item": {**item, **update}}

    def set_emoji(self, md5: str, emoji_id: str) -> None:
        """记下 QQ 收藏表情的资源 ID，后续可原样发送而不是当普通图片。"""
        lib = self._load()
        for it in lib["items"]:
            if it.get("md5") == md5:
                it["emoji_id"] = str(emoji_id or "")
                break
        self._save(lib)

    async def analyze_all(self, llm=None, progress=None) -> dict:
        """按顺序重新理解全部表情；progress(done,total,failed,name) 用于面板进度。"""
        lib = self._load()
        items = [it for it in lib["items"] if it.get("file")]
        total = len(items)
        done = failed = 0
        for idx, it in enumerate(items, start=1):
            name = str(it.get("name") or "")
            try:
                r = await self.analyze(str(it.get("md5") or ""), llm)
                if r.get("ok"):
                    done += 1
                else:
                    failed += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                log.debug("批量理解失败：%s", e)
            if progress:
                progress(idx, total, failed, name)
            await asyncio.sleep(0.2)
        return {"ok": True, "total": total, "done": done, "failed": failed}

    async def analyze_pending(self, llm=None, *, limit: int = 0) -> int:
        """按顺序给未打标的表情补分析，避免一次并发几十个视觉请求。"""
        lib = self._load()
        pending = [it for it in lib["items"] if not it.get("analyzed") and it.get("file")]
        if limit > 0:
            pending = pending[:limit]
        done = 0
        for it in pending:
            try:
                r = await self.analyze(str(it.get("md5") or ""), llm)
                if r.get("ok"):
                    done += 1
            except Exception as e:  # noqa: BLE001
                log.debug("批量打标失败：%s", e)
            await asyncio.sleep(0.2)
        return done

    def _set_analysis(self, md5: str, update: dict) -> None:
        lib = self._load()
        for it in lib["items"]:
            if it.get("md5") == md5:
                it.update(update)
                break
        self._save(lib)

    def mark_used(self, md5: str) -> None:
        lib = self._load()
        for it in lib["items"]:
            if it.get("md5") == md5:
                it["uses"] = int(it.get("uses") or 0) + 1
                it["last_used"] = time.time()
                break
        self._save(lib)

    def update(self, md5: str, *, name: str = "", tags: list[str] | None = None,
               scope: str = "", enabled: bool | None = None,
               meaning: str = "", emotion: str = "",
               use_when: str = "", avoid_when: str = "") -> dict:
        lib = self._load()
        for it in lib["items"]:
            if it.get("md5") != md5:
                continue
            if name:
                it["name"] = str(name)[:12]
            if tags is not None:
                it["tags"] = [str(t)[:8] for t in tags if str(t).strip()][:5]
            if scope in SCOPES:
                it["scope"] = scope
            if enabled is not None:
                it["enabled"] = bool(enabled)
            if meaning:
                it["meaning"] = str(meaning)[:300]
            if emotion:
                it["emotion"] = str(emotion)[:20]
            if use_when:
                it["use_when"] = str(use_when)[:200]
            if avoid_when:
                it["avoid_when"] = str(avoid_when)[:200]
            self._save(lib)
            return {"ok": True, "item": it}
        return {"ok": False, "error": "没有这张表情"}

    def delete(self, md5: str) -> dict:
        lib = self._load()
        item = next((x for x in lib["items"] if x.get("md5") == md5), None)
        if not item:
            return {"ok": False, "error": "没有这张表情"}
        p = self.root / str(item.get("file") or "")
        if p.is_file() and self.root in p.resolve().parents:
            p.unlink(missing_ok=True)
        lib["items"] = [x for x in lib["items"] if x.get("md5") != md5]
        self._save(lib)
        return {"ok": True}
