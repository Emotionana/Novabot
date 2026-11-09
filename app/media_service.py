"""媒体理解：下载图片/语音/视频，转成模型可读的文字说明。

视觉：OpenAI 兼容 /chat/completions（可选跟聊天模型同一 base_url）
语音：OpenAI Whisper 兼容 /audio/transcriptions
视频：ffmpeg 抽帧 + 抽音轨，再走视觉/语音
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import mimetypes
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path

import httpx

from .models import InboundMessage, MediaItem

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover
    Image = None
    ImageOps = None

log = logging.getLogger(__name__)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
VISION_MAX_SIDE = 1600

VISION_PROMPT = (
    "用中文简短描述这张图片/表情包：画面里有什么、有没有文字、整体情绪是什么。"
    "不要编造看不见的细节，不要输出 Markdown。"
)


def _guess_mime(data: bytes, fallback: str = "application/octet-stream") -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"OggS":
        return "audio/ogg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3"):
        return "audio/mpeg"
    return fallback


class MediaService:
    def __init__(self, db, settings, llm=None):
        self.db = db
        self.settings = settings
        self.llm = llm
        root = Path(getattr(db, "path", ".")).parent / "media"
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        # 显式声明这些 key，接线门禁才能看到；同时让配置错误在启动时暴露
        with contextlib.suppress(Exception):
            self._warm_settings()

    def _warm_settings(self) -> None:
        self.settings.get("media.enabled")
        self.settings.get("media.vision_auto")
        self.settings.get("media.image_enabled")
        self.settings.get("media.voice_enabled")
        self.settings.get("media.video_enabled")
        self.settings.get("media.vision_mode")
        self.settings.get("media.vision_base_url")
        self.settings.get("media.vision_api_key")
        self.settings.get("media.vision_model")
        self.settings.get("media.image_vision_mode")
        self.settings.get("media.image_vision_base_url")
        self.settings.get("media.image_vision_model")
        self.settings.get("media.image_vision_api_key")
        self.settings.get("media.video_vision_mode")
        self.settings.get("media.video_vision_base_url")
        self.settings.get("media.video_vision_model")
        self.settings.get("media.video_vision_api_key")
        self.settings.get("media.asr_base_url")
        self.settings.get("media.asr_api_key")
        self.settings.get("media.asr_model")
        self.settings.get("media.asr_local_python")
        self.settings.get("media.asr_local_script")
        self.settings.get("media.asr_language")
        self.settings.get("media.ffmpeg_path")
        self.settings.get("media.video_frames")
        self.settings.get("media.max_mb")

    def _get(self, key, default=None):
        try:
            value = self.settings.get(key)
            return default if value is None else value
        except KeyError:
            return default

    @property
    def enabled(self) -> bool:
        return bool(self._get("media.enabled", True))

    def kind_enabled(self, kind: str) -> bool:
        if kind in ("image", "sticker"):
            return bool(self._get("media.image_enabled", True))
        if kind == "voice":
            return bool(self._get("media.voice_enabled", True))
        if kind == "video":
            return bool(self._get("media.video_enabled", True))
        return True

    async def enrich(self, msg: InboundMessage) -> str:
        """下载并理解媒体，返回追加到当前消息的文本。失败不阻断聊天。"""
        if not self.enabled or not getattr(msg, "media", None):
            return ""
        notes: list[str] = []
        for item in msg.media:
            if not self.kind_enabled(item.kind):
                continue
            try:
                data, mime = await self._download(msg, item)
            except Exception as e:  # noqa: BLE001
                log.warning("媒体下载失败（%s）：%s", item.kind, e)
                notes.append(f"【{self._label(item.kind)}：下载失败】")
                continue
            if not data:
                continue
            if item.kind in ("image", "sticker"):
                if not bool(self._get("media.image_enabled", True)):
                    continue
                desc = await self._vision(data, mime or item.mime, sticker=item.kind == "sticker")
                notes.append(f"【{self._label(item.kind)}】{desc or '没有识别结果'}")
            elif item.kind == "voice":
                if not bool(self._get("media.voice_enabled", True)):
                    continue
                text = await self._asr(data, mime or "audio/ogg")
                notes.append(f"【语音转写】{text}" if text else "【语音：未配置语音识别或识别为空】")
            elif item.kind == "video":
                if not bool(self._get("media.video_enabled", True)):
                    continue
                notes.append(await self._video(data, mime or "video/mp4"))
        return "\n".join(n for n in notes if n)

    @staticmethod
    def _label(kind: str) -> str:
        return {"image": "图片", "sticker": "表情包", "voice": "语音", "video": "视频"}.get(kind, "媒体")

    async def _download(self, msg: InboundMessage, item: MediaItem) -> tuple[bytes, str]:
        limit = int(self._get("media.max_mb", 20) or 20) * 1024 * 1024
        raw_path = item.file or ""
        if item.url.startswith("base64://"):
            data = base64.b64decode(item.url[9:])
            return data[:limit], _guess_mime(data, item.mime)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Novabot/0.1",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": "https://q.qq.com/",
        }
        errors = []
        if item.url.startswith(("http://", "https://")):
            try:
                async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c, \
                        c.stream("GET", item.url, headers=headers) as r:
                    r.raise_for_status()
                    chunks = bytearray()
                    async for chunk in r.aiter_bytes(65536):
                        chunks.extend(chunk)
                        if len(chunks) > limit:
                            raise RuntimeError("媒体超过大小上限")
                    data = bytes(chunks)
                    ctype = r.headers.get("content-type", "")
                    return data, _guess_mime(data, ctype or item.mime)
            except Exception as e:  # noqa: BLE001
                errors.append(str(e)[:160])
        if raw_path and Path(raw_path).is_file():
            data = Path(raw_path).read_bytes()
            return data[:limit], _guess_mime(data, item.mime)
        if raw_path and raw_path.startswith(("http://", "https://")):
            try:
                async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
                    r = await c.get(raw_path, headers=headers)
                    r.raise_for_status()
                    data = r.content
                    return data[:limit], _guess_mime(data, r.headers.get("content-type", "") or item.mime)
            except Exception as e:  # noqa: BLE001
                errors.append(str(e)[:160])
        raise RuntimeError("; ".join(errors) or "没有可下载的媒体地址")

    # ---------- 视觉 ----------
    async def _prepare_vision_data(self, data: bytes, mime: str) -> tuple[bytes, str]:
        """统一转成适中尺寸的 JPEG，避免大图直接把视觉模型顶爆。"""
        if Image is not None and data:
            try:
                return await asyncio.to_thread(self._normalize_image, data)
            except Exception as e:  # noqa: BLE001
                log.debug("图片缩放失败，回退原图：%s", e)

        kind = (mime or "").lower()
        animated = kind in ("image/gif", "image/webp", "image/avif") or data.startswith((b"GIF8", b"RIFF"))
        if not animated:
            return data, mime or _guess_mime(data)
        ffmpeg = str(self._get("media.ffmpeg_path", "ffmpeg") or "ffmpeg")
        tmp = Path(tempfile.mkdtemp(prefix="pb-vision-"))
        try:
            src = tmp / "input.gif"
            src.write_bytes(data)
            out = tmp / "frame.jpg"
            proc = await asyncio.create_subprocess_exec(
                ffmpeg, "-y", "-loglevel", "error", "-i", str(src),
                "-frames:v", "1", "-vf", f"scale='min({VISION_MAX_SIDE},iw)':-2", str(out),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=_NO_WINDOW,
            )
            if await asyncio.wait_for(proc.wait(), timeout=30) == 0 and out.is_file():
                return out.read_bytes(), "image/jpeg"
        except Exception as e:  # noqa: BLE001
            log.debug("动图转静帧失败：%s", e)
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
        return data, mime or _guess_mime(data)

    @staticmethod
    def _normalize_image(data: bytes) -> tuple[bytes, str]:
        im = Image.open(BytesIO(data))
        im.seek(0)
        im = ImageOps.exif_transpose(im)
        im.thumbnail((VISION_MAX_SIDE, VISION_MAX_SIDE), Image.Resampling.LANCZOS)
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", rgba.size, "white")
            bg.paste(rgba, mask=rgba.getchannel("A"))
            im = bg
        else:
            im = im.convert("RGB")
        out = BytesIO()
        im.save(out, "JPEG", quality=88, optimize=True, progressive=False)
        raw = out.getvalue()
        if len(raw) > 3 * 1024 * 1024:
            im.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            out = BytesIO()
            im.save(out, "JPEG", quality=80, optimize=True, progressive=False)
            raw = out.getvalue()
        return raw, "image/jpeg"

    async def _vision(self, data: bytes, mime: str, *, sticker: bool = False, kind: str = "image") -> str:
        prompt = ("这是一个 QQ 表情包。\n" if sticker else "") + VISION_PROMPT
        return await self.vision_text(data, mime, prompt, kind=kind)

    def _vision_mode(self, kind: str) -> str:
        per = str(self._get(f"media.{kind}_vision_mode", "") or "").strip()
        return per or str(self._get("media.vision_mode", "dsh") or "dsh")

    def _kind_value(self, kind: str, name: str, default=None):
        value = self._get(f"media.{kind}_vision_{name}", None)
        if value not in (None, ""):
            return value
        return self._get(f"media.vision_{name}", default)

    async def vision_text(self, data: bytes, mime: str, prompt: str, *, max_tokens: int = 500, kind: str = "image") -> str:
        """给视觉模型一段自定义 prompt，返回文本。"""
        mode = self._vision_mode(kind)
        if mode == "off":
            return "视觉读取已关闭"
        data, mime = await self._prepare_vision_data(data, mime)
        mime = mime or _guess_mime(data)
        if mode == "dsh":
            try:
                text = await self._dsh_vision(data, mime, prompt, max_tokens=max_tokens)
                if text:
                    return text
            except Exception as e:  # noqa: BLE001
                log.warning("DSH 视觉失败，回退单独视觉接口：%s", e)
        cfg = self._vision_config(mode, kind)
        if cfg is None:
            return "未配置视觉模型"
        base, key, model = cfg
        b64 = base64.b64encode(data).decode()
        payload = {
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }],
            "max_tokens": int(max_tokens),
            "temperature": 0.1,
        }
        try:
            async with httpx.AsyncClient(timeout=90) as c:
                headers = {"Content-Type": "application/json"}
                if key:
                    headers["Authorization"] = f"Bearer {key}"
                r = await c.post(base.rstrip("/") + "/chat/completions", json=payload, headers=headers)
                if r.status_code != 200:
                    return f"视觉接口 HTTP {r.status_code}"
                msg = ((r.json().get("choices") or [{}])[0].get("message") or {})
                text = msg.get("content") or ""
                if isinstance(text, list):
                    text = "".join(x.get("text", "") for x in text if isinstance(x, dict))
                return str(text).strip()[:1200]
        except Exception as e:  # noqa: BLE001
            log.warning("视觉识别失败：%s", e)
            return "视觉识别失败"

    async def _dsh_vision(self, data: bytes, mime: str, prompt: str, *, max_tokens: int = 500) -> str:
        """把图片直接交给当前 DSH Agent 的模型。"""
        endpoint = str(getattr(self.llm, "endpoint", "") or self._get("llm.agent_endpoint", "") or "").strip()
        if not endpoint:
            raise RuntimeError("没有 DSH endpoint")
        base = endpoint.rstrip("/")
        if base.endswith("/chat"):
            base = base[:-5]
        if not base.endswith("/vision"):
            base = base.rstrip("/") + "/vision"
        token = str(getattr(self.llm, "token", "") or self._get("llm.agent_token", "") or "").strip()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
            headers["X-Platform-Token"] = token
        payload = {
            "prompt": prompt,
            "image": {"data": base64.b64encode(data).decode(), "mimeType": mime or "image/jpeg"},
            "max_tokens": int(max_tokens),
            "temperature": 0.1,
        }
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(base, json=payload, headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"DSH 视觉 HTTP {r.status_code}")
        return str(r.json().get("reply") or "").strip()[:2000]

    def _vision_config(self, mode: str, kind: str = "image"):
        if mode in ("custom", "chat", "dsh"):
            custom = str(self._kind_value(kind, "base_url", "") or "").strip()
            if custom:
                model = str(self._kind_value(kind, "model", "") or "").strip()
                if not model:
                    return None
                return custom, str(self._kind_value(kind, "api_key", "") or ""), model
        if mode == "chat":
            # OpenAI 兼容的聊天适配器可以直接复用 base_url/model
            base = str(getattr(self.llm, "base_url", "") or "").strip()
            model = str(getattr(self.llm, "model", "") or "").strip()
            if base and model:
                return base, str(getattr(self.llm, "api_key", "") or ""), model
        return None

    # ---------- 语音 ----------
    async def _asr(self, data: bytes, mime: str) -> str:
        base = str(self._get("media.asr_base_url", "") or "").strip()
        if base:
            model = str(self._get("media.asr_model", "whisper-1") or "whisper-1")
            language = str(self._get("media.asr_language", "zh") or "zh")
            headers = {}
            key = str(self._get("media.asr_api_key", "") or "")
            if key:
                headers["Authorization"] = f"Bearer {key}"
            ext = mimetypes.guess_extension(mime or "audio/ogg") or ".ogg"
            try:
                async with httpx.AsyncClient(timeout=120) as c:
                    r = await c.post(
                        base.rstrip("/") + "/audio/transcriptions",
                        headers=headers,
                        data={"model": model, "language": language},
                        files={"file": (f"voice{ext}", data, mime or "audio/ogg")},
                    )
                    if r.status_code == 200:
                        return str(r.json().get("text") or "").strip()[:1000]
                    log.warning("语音识别 HTTP %s", r.status_code)
            except Exception as e:  # noqa: BLE001
                log.warning("语音识别接口失败：%s", e)
        return await self._asr_local(data, mime)

    async def _asr_local(self, data: bytes, mime: str) -> str:
        """本地 faster-whisper：先转 16k wav，再调用用户配置的 Python 脚本。"""
        python = str(self._get("media.asr_local_python", "") or "").strip()
        script = str(self._get("media.asr_local_script", "") or "").strip()
        if not python or not script or not Path(python).is_file() or not Path(script).is_file():
            return ""
        ffmpeg = str(self._get("media.ffmpeg_path", "ffmpeg") or "ffmpeg")
        tmp = Path(tempfile.mkdtemp(prefix="pb-asr-"))
        try:
            src = tmp / ("voice" + (mimetypes.guess_extension(mime or "audio/ogg") or ".ogg"))
            src.write_bytes(data)
            wav = tmp / "voice.wav"
            try:
                conv = await asyncio.create_subprocess_exec(
                    ffmpeg, "-y", "-loglevel", "error", "-i", str(src),
                    "-vn", "-ac", "1", "-ar", "16000", str(wav),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=_NO_WINDOW,
                )
                ok = await asyncio.wait_for(conv.wait(), timeout=60) == 0 and wav.is_file()
            except Exception:  # noqa: BLE001
                ok = False
            target = wav if ok else src
            proc = await asyncio.create_subprocess_exec(
                python, script, str(target),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                creationflags=_NO_WINDOW,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=180)
            if proc.returncode != 0:
                log.warning("本地语音识别失败：%s", (err or b"")[-300:].decode("utf-8", "ignore"))
                return ""
            return out.decode("utf-8", "ignore").strip()[:1000]
        except Exception as e:  # noqa: BLE001
            log.warning("本地语音识别出错：%s", e)
            return ""
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    # ---------- 视频 ----------
    async def _video(self, data: bytes, mime: str) -> str:
        ffmpeg = str(self._get("media.ffmpeg_path", "ffmpeg") or "ffmpeg")
        frames = max(1, min(6, int(self._get("media.video_frames", 3) or 3)))
        tmp = Path(tempfile.mkdtemp(prefix="pb-video-"))
        video = tmp / ("input" + (mimetypes.guess_extension(mime) or ".mp4"))
        video.write_bytes(data)
        try:
            # 检查 ffmpeg
            probe = await asyncio.create_subprocess_exec(
                ffmpeg, "-version", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW
            )
            if await probe.wait() != 0:
                return "视频：没有找到 ffmpeg，先装 ffmpeg 或填 media.ffmpeg_path"
            descriptions: list[str] = []
            for i in range(frames):
                out = tmp / f"frame{i}.jpg"
                # 取视频中段附近均匀抽帧
                sec = 1 + i * 2
                proc = await asyncio.create_subprocess_exec(
                    ffmpeg, "-y", "-loglevel", "error", "-ss", str(sec), "-i", str(video),
                    "-frames:v", "1", "-vf", "scale=640:-2", str(out),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=_NO_WINDOW,
                )
                if await proc.wait() == 0 and out.is_file():
                    descriptions.append(await self._vision(out.read_bytes(), "image/jpeg", kind="video"))
            audio = tmp / "audio.wav"
            proc = await asyncio.create_subprocess_exec(
                ffmpeg, "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(audio),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=_NO_WINDOW,
            )
            transcript = ""
            if await proc.wait() == 0 and audio.is_file():
                transcript = await self._asr(audio.read_bytes(), "audio/wav")
            parts = [f"画面{idx + 1}：{d}" for idx, d in enumerate(descriptions) if d]
            if transcript:
                parts.append(f"音轨转写：{transcript}")
            return "视频：" + ("；".join(parts) if parts else "没有抽到可读内容")
        except Exception as e:  # noqa: BLE001
            log.warning("视频处理失败：%s", e)
            return "视频：处理失败"
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
