"""GPT-SoVITS 引擎。

走 api_v2 的 GET /tts：
    /tts?text=...&text_lang=zh&ref_audio_path=...&prompt_lang=zh&prompt_text=...
        &top_k=5&temperature=1.0&speed_factor=1.0&seed=-1&media_type=wav

只下发它支持的参数（caps 白名单），参考音里多配的字段自动忽略。
"""

from __future__ import annotations

import logging

import httpx

from .base import EngineCaps, TTSParams, VoiceError

log = logging.getLogger(__name__)

# api_v2 的 /tts 参数（也就是我们允许下发的白名单）
_ALLOWED = frozenset(
    {
        "text",
        "text_lang",
        "ref_audio_path",
        "prompt_lang",
        "prompt_text",
        "top_k",
        "top_p",
        "temperature",
        "text_split_method",
        "batch_size",
        "batch_threshold",
        "split_bucket",
        "speed_factor",
        "fragment_interval",
        "seed",
        "media_type",
        "parallel_infer",
        "repetition_penalty",
        "sample_steps",
        "super_sampling",
        "streaming_mode",
        "overlap_length",
        "min_chunk_length",
    }
)


class GPTSoVITSEngine:
    name = "gptsovits"
    caps = EngineCaps(name="gptsovits", params=_ALLOWED, supports_speed=True)

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

    # -- 生命周期 --------------------------------------------------------

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
            r = await self._http().get(f"{self.url}/docs", timeout=3.0)
            return r.status_code < 500
        except Exception:
            return False

    # -- 合成 ------------------------------------------------------------

    def _query(self, params: TTSParams) -> dict:
        """把 TTSParams 变成发得出去的 query。只留引擎认识的名字。"""
        q: dict = {
            "text": params.text,
            "text_lang": params.text_lang or "zh",
            "ref_audio_path": params.ref_audio,
            "prompt_lang": params.prompt_lang or "zh",
            "prompt_text": params.prompt_text or "",
            "media_type": "wav",
            "streaming_mode": False,
        }
        merged = {**self.defaults, **params.options}
        # 通用参数映射
        if "speed" in merged:
            q["speed_factor"] = merged.pop("speed")
        for key, value in merged.items():
            if key in _ALLOWED:
                q[key] = value
            elif key not in ("speed",):
                log.debug("引擎 %s 不支持参数 %s，已忽略", self.name, key)
        return q

    async def synth(self, params: TTSParams) -> bytes:
        if not self.url:
            raise VoiceError("没配 GPT-SoVITS 地址")
        if not params.ref_audio:
            raise VoiceError("这条参考音没有音频路径")

        try:
            r = await self._http().get(f"{self.url}/tts", params=self._query(params))
        except httpx.TimeoutException as e:
            raise VoiceError(f"合成超时（{self.timeout}s）") from e
        except httpx.HTTPError as e:
            raise VoiceError(f"连不上引擎：{e}") from e

        if r.status_code != 200:
            raise VoiceError(f"引擎返回 {r.status_code}：{r.text[:160]}")
        data = r.content
        if len(data) < 100:
            raise VoiceError(f"返回内容太短，可能失败了：{data[:80]!r}")
        return data
