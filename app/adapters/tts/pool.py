"""引擎池：按参考音指定的引擎合成，失败按配置回退。

回退会让音色变（GPT-SoVITS → Edge 完全不是一个人），所以回退必须打 warning，
不能静默——design/05-语音.md 第二节。
"""

from __future__ import annotations

import contextlib
import logging

from .base import EngineCaps, TTSEngine, TTSParams, VoiceError

log = logging.getLogger(__name__)


class TTSPool:
    def __init__(
        self,
        engines: dict[str, TTSEngine],
        *,
        fallback: list[str] | None = None,
        allow_fallback: bool = True,
    ):
        self.engines = dict(engines)
        self.fallback = [n for n in (fallback or []) if n]
        self.allow_fallback = allow_fallback

    def get(self, name: str) -> TTSEngine | None:
        return self.engines.get(name) or None

    def names(self) -> list[str]:
        return list(self.engines)

    def caps(self, name: str) -> EngineCaps | None:
        engine = self.get(name)
        return engine.caps() if engine else None

    async def synth(self, params: TTSParams) -> tuple[bytes, str]:
        """返回 (音频, 实际用的引擎)。全都失败才抛 VoiceError。"""
        order = [params.engine] if params.engine else []
        if self.allow_fallback:
            order += [n for n in self.fallback if n not in order]
        if not order or not any(self.get(n) for n in order):
            order = list(self.engines)

        last = ""
        for name in order:
            engine = self.get(name)
            if engine is None:
                continue
            try:
                audio = await engine.synth(params)
                if name != params.engine and params.engine:
                    log.warning("引擎 %s 失败，回退到 %s（音色会变）", params.engine, name)
                return audio, name
            except VoiceError as e:
                last = f"{name}: {e}"
                log.warning("引擎 %s 合成失败：%s", name, e)
            except Exception as e:  # noqa: BLE001
                last = f"{name}: {e}"
                log.exception("引擎 %s 异常", name)

        raise VoiceError(f"所有引擎都失败（{last or '没配引擎'}）")

    async def close(self) -> None:
        for engine in self.engines.values():
            with contextlib.suppress(Exception):
                await engine.close()
