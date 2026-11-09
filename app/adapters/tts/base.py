"""语音引擎接口。

分层（见 design/05-语音.md）：
    selector 决定用哪条参考音、什么参数
    engine   只负责「把文字 + 参考音合成一段音频」

引擎差异靠 capabilities() 描述：不支持的参数发之前丢掉，不报错。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class VoiceError(Exception):
    """合成失败。上层据此回退到文字。"""


@dataclass(frozen=True)
class EngineCaps:
    """引擎支持什么。params 是允许下发的参数名集合。"""

    name: str = ""
    params: frozenset[str] = frozenset()
    supports_speed: bool = True


@dataclass
class TTSParams:
    """一次合成请求。"""

    text: str
    ref_audio: str = ""
    prompt_text: str = ""
    prompt_lang: str = "zh"
    text_lang: str = "zh"
    engine: str = ""
    # 参考音上配的参数（seed / top_k / temperature / speed ...）
    options: dict = field(default_factory=dict)


@runtime_checkable
class TTSEngine(Protocol):
    name: str

    def caps(self) -> EngineCaps: ...

    async def synth(self, params: TTSParams) -> bytes: ...

    async def health(self) -> bool: ...

    async def close(self) -> None: ...
