"""OpenAI 兼容 /v1/audio/speech。

Fish Speech、CosyVoice 的 OpenAI 兼容层、Kokoro、本地网关都能接。
请求 WAV，统一 normalize。
"""

from __future__ import annotations

import logging

import httpx

from .base import EngineCaps, TTSParams, VoiceError

log = logging.getLogger(__name__)

_ALLOWED = frozenset({"model", "voice", "speed", "instructions", "response_format"})


class OpenAIEngine:
    name = "openai"
    caps = EngineCaps(name="openai", params=_ALLOWED, supports_speed=True)

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        model: str = "",
        voice: str = "",
        timeout: float = 120.0,
        defaults: dict | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.voice = voice or ""
        self.timeout = timeout
        self.defaults = dict(defaults or {})
        self._client = client

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=10.0))
        return self._client

    def _url(self) -> str:
        if self.base_url.endswith("/audio/speech"):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return self.base_url + "/audio/speech"
        return self.base_url + "/v1/audio/speech"

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def health(self) -> bool:
        if not self.base_url:
            return False
        base = self.base_url
        if base.endswith("/audio/speech"):
            base = base[: -len("/audio/speech")]
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        try:
            r = await self._http().get(base + "/v1/models", timeout=3.0)
            return r.status_code < 500
        except Exception:
            return False

    async def synth(self, params: TTSParams) -> bytes:
        if not self.base_url:
            raise VoiceError("没配 OpenAI 兼容 TTS 地址")
        merged = {**self.defaults, **params.options}
        body = {
            "model": merged.get("model") or self.model,
            "input": params.text,
            "voice": merged.get("voice") or self.voice,
            "response_format": "wav",
        }
        if "speed" in merged:
            body["speed"] = merged["speed"]
        if "instructions" in merged:
            body["instructions"] = merged["instructions"]
        for key in _ALLOWED:
            if key in merged and key not in body:
                body[key] = merged[key]
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            r = await self._http().post(self._url(), json=body, headers=headers)
        except httpx.TimeoutException as e:
            raise VoiceError(f"OpenAI TTS 超时（{self.timeout}s）") from e
        except httpx.HTTPError as e:
            raise VoiceError(f"连不上 OpenAI TTS：{e}") from e
        if r.status_code != 200:
            raise VoiceError(f"OpenAI TTS 返回 {r.status_code}：{r.text[:160]}")
        if len(r.content) < 100:
            raise VoiceError(f"返回音频太短：{r.content[:80]!r}")
        return r.content
