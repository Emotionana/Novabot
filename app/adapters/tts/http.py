"""通用 HTTP TTS：地址/方法/请求头/请求体由用户填。

- GET：地址里必须含 {text}
- POST：请求体模板支持 {text}（JSON 转义）、{text_raw}、{ref_audio}、{prompt_text}
- 返回二进制音频，或 JSON 里带 base64（自动从 data/audio/... 挖）
"""

from __future__ import annotations

import base64
import json
import logging
from urllib.parse import quote

import httpx

from .audio import pcm16_to_wav
from .base import EngineCaps, TTSParams, VoiceError

log = logging.getLogger(__name__)

_ALLOWED = frozenset({"speed"})


def _headers(raw) -> dict:
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if v}
    out = {}
    for line in str(raw or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or ":" not in s:
            continue
        k, v = s.split(":", 1)
        if k.strip():
            out[k.strip()] = v.strip()
    return out


def _find_b64(obj, depth=0) -> str:
    if depth > 4 or not isinstance(obj, (dict, list)):
        return ""
    if isinstance(obj, list):
        for item in obj:
            got = _find_b64(item, depth + 1)
            if got:
                return got
        return ""
    for key in ("data", "audio", "audio_base64", "audioBase64", "base64", "voice", "result"):
        v = obj.get(key)
        if isinstance(v, str) and len(v) > 200:
            return v
        got = _find_b64(v, depth + 1)
        if got:
            return got
    for v in obj.values():
        if isinstance(v, (dict, list)):
            got = _find_b64(v, depth + 1)
            if got:
                return got
    return ""


class HTTPEngine:
    name = "http"
    caps = EngineCaps(name="http", params=_ALLOWED, supports_speed=False)

    def __init__(
        self,
        *,
        url: str,
        timeout: float = 120.0,
        defaults: dict | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.url = (url or "").strip()
        self.timeout = timeout
        self.defaults = dict(defaults or {})
        self._client = client

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=10.0))
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def health(self) -> bool:
        if not self.url:
            return False
        base = self.url.split("{", 1)[0] or self.url
        try:
            r = await self._http().get(base, timeout=3.0)
            return r.status_code < 500
        except Exception:
            return False

    async def synth(self, params: TTSParams) -> bytes:
        if not self.url:
            raise VoiceError("没配 TTS 地址")
        cfg = {**self.defaults, **params.options}
        method = str(cfg.get("method") or "POST").upper()
        headers = _headers(cfg.get("headers"))
        target = self.url
        body = None

        if method == "GET":
            if "{text}" not in target:
                raise VoiceError("GET 地址里要含 {text}")
            target = target.replace("{text}", quote(params.text))
        else:
            raw = str(cfg.get("body") or "").strip()
            if not raw:
                raw = json.dumps({"text": "{text}"})
            escaped = json.dumps(params.text)[1:-1]
            body = (
                raw.replace("{text}", escaped)
                .replace("{text_raw}", params.text)
                .replace("{ref_audio}", params.ref_audio)
                .replace("{prompt_text}", params.prompt_text)
            )
            if not any(k.lower() == "content-type" for k in headers):
                headers["content-type"] = "application/json"

        try:
            r = await self._http().request(method, target, headers=headers, content=body)
        except httpx.TimeoutException as e:
            raise VoiceError(f"TTS 超时（{self.timeout}s）") from e
        except httpx.HTTPError as e:
            raise VoiceError(f"连不上 TTS：{e}") from e
        if r.status_code != 200:
            raise VoiceError(f"TTS 返回 {r.status_code}：{r.text[:160]}")

        data = r.content
        ctype = (r.headers.get("content-type") or "").lower()
        if "json" in ctype or data[:1] == b"{":
            try:
                obj = json.loads(data.decode("utf-8", errors="replace"))
            except json.JSONDecodeError as e:
                raise VoiceError(f"返回 JSON 解析失败：{data[:120]!r}") from e
            b64 = _find_b64(obj)
            if not b64:
                raise VoiceError("返回 JSON 里没找到音频 base64")
            try:
                data = base64.b64decode(b64)
            except Exception as e:  # noqa: BLE001
                raise VoiceError(f"base64 解码失败：{e}") from e
        if len(data) < 100:
            raise VoiceError(f"返回音频太短：{data[:80]!r}")
        return pcm16_to_wav(data, int(cfg.get("sample_rate") or 24000))
