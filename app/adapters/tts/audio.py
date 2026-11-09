"""音频处理：统一成 QQ 能发的 32k 单声道 wav。

不同引擎出来的采样率/声道不一样（GPT-SoVITS 常见 32k 单声道，Edge 24k），
按 design/05-语音.md 第九节：发之前统一转 32k/mono。
"""

from __future__ import annotations

import io

import numpy as np
import soundfile as sf

from .base import VoiceError

TARGET_RATE = 32000


def pcm16_to_wav(data: bytes, sample_rate: int = 24000) -> bytes:
    """裸 PCM16 mono → WAV；已经是 WAV 就原样返回。"""
    if data[:4] == b"RIFF":
        return data
    pcm = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    out = io.BytesIO()
    sf.write(out, pcm, int(sample_rate or 24000), format="WAV", subtype="PCM_16")
    return out.getvalue()


def normalize_wav(data: bytes, *, target_rate: int = TARGET_RATE) -> tuple[bytes, float]:
    """返回 (wav 字节, 时长秒)。失败抛 VoiceError。"""
    if not data:
        raise VoiceError("音频是空的")
    try:
        audio, rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except Exception as e:
        raise VoiceError(f"读不出音频：{e}") from e

    if audio.size == 0:
        raise VoiceError("音频没有采样")

    mono = audio.mean(axis=1)
    duration = len(mono) / float(rate or 1)

    if rate != target_rate and len(mono) > 1:
        n = max(1, int(round(duration * target_rate)))
        old_x = np.linspace(0.0, 1.0, len(mono), endpoint=False)
        new_x = np.linspace(0.0, 1.0, n, endpoint=False)
        mono = np.interp(new_x, old_x, mono).astype(np.float32)
        rate = target_rate
        duration = n / float(target_rate)

    # 防削波，但别把很轻的录音硬拉大
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    if peak > 0.99:
        mono = (mono / peak * 0.99).astype(np.float32)

    out = io.BytesIO()
    sf.write(out, mono, rate, format="WAV", subtype="PCM_16")
    return out.getvalue(), duration
