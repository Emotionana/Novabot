"""B 站翻唱模块：网易云搜歌、下载、音色库、外部换声引擎任务。

不内置几 GB 的 RVC/Applio；换声走一个明确的 HTTP 引擎协议，避免把
Novabot 主进程写成安装器。UI 会明确显示引擎是否配置。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
import uuid
import zipfile
from pathlib import Path

import httpx

from .bili import BiliClient

log = logging.getLogger(__name__)

NETEASE_SEARCH = "https://music.163.com/api/cloudsearch/pc"
NETEASE_OUTER = "https://music.163.com/song/media/outer/url?id={}.mp3"


def safe_name(value: str, fallback: str = "voice") -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", str(value or "")).strip("._")
    return (text or fallback)[:80]


def parse_search(data: dict, limit: int = 8) -> list[dict]:
    songs = ((data or {}).get("result") or {}).get("songs") or []
    out = []
    for s in songs[:limit]:
        artists = " / ".join(a.get("name", "") for a in (s.get("ar") or []) if a.get("name"))
        out.append({
            "id": s.get("id"),
            "name": s.get("name") or "",
            "artists": artists,
            "album": (s.get("al") or {}).get("name") or "",
            "duration_sec": round((s.get("dt") or 0) / 1000),
            "vip": s.get("fee") in (1, 4),
        })
    return out


class SongModule:
    def __init__(self, db, settings, account_id: str = ""):
        self.db = db
        self.settings = settings
        self.account_id = account_id or getattr(settings, "account_id", "") or "default"
        safe = re.sub(r"[^\w.-]+", "_", str(self.account_id))[:80] or "default"
        root = Path(getattr(db, "path", ".")).parent / "modules" / "song" / safe
        self.root = root
        self.models = root / "models"
        self.cache = root / "cache"
        for d in (self.models, self.cache):
            d.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, dict] = {}
        self.history: list[dict] = []
        self.bili = BiliClient(settings)

    # ---------- 配置 ----------
    def _enabled(self) -> bool:
        return bool(self.settings.get("module.song_enabled"))

    def _engine(self) -> str:
        return str(self.settings.get("module.song_engine_url") or "").strip()

    def _cookie(self) -> str:
        return str(self.settings.get("module.netease_cookie") or "").strip()

    def _headers(self) -> dict:
        h = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
            "Referer": "https://music.163.com/",
        }
        cookie = self._cookie()
        if cookie:
            h["Cookie"] = cookie if "=" in cookie else f"MUSIC_U={cookie}"
        return h

    # ---------- 音色 ----------
    def voices(self) -> list[dict]:
        out = []
        for p in sorted(self.models.glob("*.pth")):
            idx = self.models / f"{p.stem}.index"
            out.append({
                "id": p.stem,
                "file": p.name,
                "has_index": idx.exists(),
                "size": p.stat().st_size,
            })
        return out

    def import_voice(self, filename: str, data: bytes) -> dict:
        name = safe_name(Path(filename).name)
        lower = name.lower()
        if len(data) < 1024:
            return {"ok": False, "error": "文件太小，不像模型"}
        if lower.endswith(".zip"):
            try:
                with zipfile.ZipFile(__import__("io").BytesIO(data)) as z:
                    members = [m for m in z.infolist() if m.filename.lower().endswith((".pth", ".index"))]
                    if not members:
                        return {"ok": False, "error": "zip 里没有 .pth / .index"}
                    pths = [m for m in members if m.filename.lower().endswith(".pth")]
                    if not pths:
                        return {"ok": False, "error": "zip 里没有 .pth 模型"}
                    for m in members:
                        target = self.models / safe_name(Path(m.filename).name)
                        target.write_bytes(z.read(m))
                    return {"ok": True, "added": [safe_name(Path(m.filename).stem) for m in pths]}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"解压失败：{e}"}
        if not lower.endswith((".pth", ".index")):
            return {"ok": False, "error": "只支持 .pth / .index / .zip"}
        (self.models / name).write_bytes(data)
        return {"ok": True, "added": [Path(name).stem]}

    def delete_voice(self, voice_id: str) -> dict:
        p = self.models / f"{safe_name(voice_id)}.pth"
        idx = self.models / f"{safe_name(voice_id)}.index"
        if not p.exists():
            return {"ok": False, "error": "没有这个音色"}
        p.unlink(missing_ok=True)
        idx.unlink(missing_ok=True)
        return {"ok": True}

    # ---------- 状态 ----------
    def status(self) -> dict:
        engine = self._engine()
        voices = self.voices()
        enabled = self._enabled()
        extract_ready = enabled
        ready = bool(enabled and engine and voices)
        if not enabled:
            message = "B 站音频模块还没启用"
        elif not engine:
            message = "可以搜 B 站视频并提取音频；换声引擎没配置，暂时不能 RVC 翻唱"
        elif not voices:
            message = "可以搜 B 站视频并提取音频；还没音色，暂时不能 RVC 翻唱"
        else:
            message = "可以搜 B 站视频、提取音频或 RVC 翻唱"
        return {
            "ok": True,
            "enabled": enabled,
            "engine_configured": bool(engine),
            "ready": ready,
            "extract_ready": extract_ready,
            "message": message,
            "voices": voices,
            "default_voice": str(self.settings.get("module.song_default_voice") or ""),
            "default_pitch": int(self.settings.get("module.song_pitch") or 0),
            "owner_only": bool(self.settings.get("module.song_owner_only")),
            "history": self.history[-8:],
            "jobs": [j for j in self.jobs.values() if j.get("status") in ("queued", "downloading", "converting")],
        }

    async def search(self, q: str, limit: int = 8) -> list[dict]:
        q = (q or "").strip()
        if not q:
            return []
        return await self.bili.search(q, limit)

    async def download(self, song_id: int | str, dest: Path) -> Path:
        """下载 B 站视频音频。song_id 是 BV 号/链接/av 号。"""
        await self.bili.download_audio(bvid=str(song_id), dest=dest)
        return dest

    def start(self, *, song_id, name, voice, pitch, acapella=False, extract_only=False) -> dict:
        if not self._enabled():
            return {"ok": False, "error": "B 站音频模块还没启用"}
        if not extract_only:
            if not self._engine():
                return {"ok": False, "error": "还没配置换声引擎（设置 → 模块 → 翻唱引擎地址）"}
            voice = safe_name(voice)
            pth = self.models / f"{voice}.pth"
            if not pth.exists():
                return {"ok": False, "error": "没有这个音色"}
        else:
            voice = ""
        job_id = uuid.uuid4().hex[:12]
        job = {
            "id": job_id, "song": str(name or song_id), "song_id": song_id,
            "voice": voice, "pitch": int(pitch or 0), "acapella": bool(acapella),
            "status": "queued", "stage": "排队", "error": "", "file": "",
            "extract_only": bool(extract_only), "at": time.time(),
        }
        self.jobs[job_id] = job
        asyncio.create_task(self._run(job))
        return {"ok": True, "job": job}

    async def _run(self, job: dict) -> None:
        job_id = job["id"]
        try:
            job.update(status="downloading", stage="从 B 站下载音频")
            src = self.cache / f"{job_id}.m4a"
            await self.download(job["song_id"], src)
            if job.get("extract_only"):
                job.update(status="done", stage="音频已提取", file=src.name, at=time.time())
                self.history.append({k: job.get(k) for k in ("id", "song", "voice", "pitch", "file", "at")})
                return
            job.update(status="converting", stage="换声/混音")
            out = self.cache / f"{job_id}.wav"
            await self._convert(job, src, out)
            job.update(status="done", stage="完成", file=out.name, at=time.time())
            self.history.append({k: job.get(k) for k in ("id", "song", "voice", "pitch", "file", "at")})
        except Exception as e:  # noqa: BLE001
            log.warning("B 站音频任务 %s 失败：%s", job_id, e)
            job.update(status="error", stage="失败", error=str(e)[:300])

    async def _convert(self, job: dict, src: Path, out: Path) -> None:
        url = self._engine()
        pth = self.models / f"{job['voice']}.pth"
        idx = self.models / f"{job['voice']}.index"
        headers = {}
        token = str(self.settings.get("module.song_engine_token") or "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        timeout = float(self.settings.get("module.song_timeout_sec") or 1200)
        data = {
            "song": job["song"], "voice": job["voice"], "voice_path": str(pth),
            "index_path": str(idx) if idx.exists() else "",
            "pitch": str(job["pitch"]), "acapella": "1" if job["acapella"] else "0",
        }
        async with httpx.AsyncClient(timeout=timeout) as c:
            with src.open("rb") as f:
                r = await c.post(url, data=data, files={"audio": (src.name, f, "audio/mp4" if src.suffix == ".m4a" else "audio/mpeg")}, headers=headers)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if ctype.startswith("audio/"):
                out.write_bytes(r.content)
                return
            payload = r.json()
            if payload.get("audio_url"):
                rr = await c.get(payload["audio_url"])
                rr.raise_for_status()
                out.write_bytes(rr.content)
            elif payload.get("audio_base64"):
                out.write_bytes(base64.b64decode(payload["audio_base64"]))
            elif payload.get("path") and Path(payload["path"]).is_file():
                out.write_bytes(Path(payload["path"]).read_bytes())
            else:
                raise RuntimeError("引擎没有返回音频（需要 audio/* 或 audio_url / audio_base64 / path）")

    def file(self, name: str) -> Path | None:
        p = self.cache / safe_name(name, "song.wav")
        return p if p.is_file() else None
