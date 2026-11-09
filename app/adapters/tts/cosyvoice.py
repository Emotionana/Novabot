"""CosyVoice 官方 FastAPI：/inference_sft 或 /inference_instruct。"""

from __future__ import annotations

import logging

import httpx

from .audio import pcm16_to_wav
from .base import EngineCaps, TTSParams, VoiceError

log = logging.getLogger(__name__)

_ALLOWED = frozenset({"spk_id", "instruct", "mode", "speed"})


class CosyVoiceEngine:
    name = "cosyvoice"
    caps = EngineCaps(name="cosyvoice", params=_ALLOWED, supports_speed=False)

    def __init__(
        self,
        *,
        url: str,
        timeout: float = 120.0,
        defaults: dict | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.url = (url or "").rstrip("/")
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
        try:
            r = await self._http().get(self.url + "/docs", timeout=3.0)
            return r.status_code < 500
        except Exception:
            return False

    async def synth(self, params: TTSParams) -> bytes:
        if not self.url:
            raise VoiceError("没配 CosyVoice 地址")
        cfg = {**self.defaults, **params.options}
        mode = str(cfg.get("mode") or "sft").lower()
        data = {"tts_text": params.text, "spk_id": str(cfg.get("spk_id") or "中文女")}
        path = "/inference_sft"
        if mode == "instruct" and cfg.get("instruct"):
            data["instruct_text"] = str(cfg["instruct"])
            path = "/inference_instruct"
        try:
            r = await self._http().post(self.url + path, data=data)
        except httpx.TimeoutException as e:
            raise VoiceError(f"CosyVoice 超时（{self.timeout}s）") from e
        except httpx.HTTPError as e:
            raise VoiceError(f"连不上 CosyVoice：{e}") from e
        if r.status_code != 200:
            raise VoiceError(f"CosyVoice 返回 {r.status_code}：{r.text[:160]}")
        if len(r.content) < 100:
            raise VoiceError(f"返回音频太短：{r.content[:80]!r}")
        return pcm16_to_wav(r.content, int(cfg.get("sample_rate") or 22050))
