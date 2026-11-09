"""语音冒烟：直接调本机 GPT-SoVITS 合成一句话，检查音频能用。

用法（ref 和 prompt 必填，或者先用 --from-db 从库里拿第一条参考音）：
    python -m scripts.voice_smoke --ref "C:\\...\\ref.wav" \
        --prompt "参考音频对应的文本" --text "你好呀，我是小助手"

    python -m scripts.voice_smoke --from-db --text "你好"

它会：
    1. 调引擎 /tts
    2. 统一成 32k 单声道
    3. 写一个 wav 到 data/voice-smoke.wav，打印时长和大小
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.params  # noqa: E402,F401
from app.adapters.tts.audio import normalize_wav  # noqa: E402
from app.adapters.tts.base import TTSParams, VoiceError  # noqa: E402
from app.adapters.tts.gptsovits import GPTSoVITSEngine  # noqa: E402


def ref_from_db() -> tuple[str, str, str]:
    """从库里取第一条启用中的参考音：(audio_path, prompt_text, engine)。"""
    from app.db import get_db

    db = get_db()
    row = db.query_one(
        "SELECT audio_path, prompt_text, engine FROM voice_refs "
        "WHERE enabled = 1 ORDER BY updated_at DESC LIMIT 1"
    )
    if row is None:
        return "", "", ""
    return row["audio_path"], row["prompt_text"], row["engine"]


async def run(args) -> int:
    ref, prompt, engine = args.ref, args.prompt, args.engine
    if args.from_db:
        db_ref, db_prompt, db_engine = ref_from_db()
        ref = ref or db_ref
        prompt = prompt or db_prompt
        engine = engine or db_engine

    if not ref:
        print("！没有参考音：传 --ref，或者先往库里加一条参考音")
        return 2
    if not Path(ref).is_file():
        print(f"！参考音文件不存在：{ref}")
        return 2

    print(f"引擎 {args.url}  参考音 {ref}")
    print(f"参考文本 {prompt[:40]}...")

    engine_obj = GPTSoVITSEngine(url=args.url, timeout=args.timeout)
    try:
        healthy = await engine_obj.health()
        print(f"健康检查：{'OK' if healthy else '失败'}")
        if not healthy:
            print("！引擎没响应，先确认 GPT-SoVITS 起没起")
            return 2

        started = time.time()
        audio = await engine_obj.synth(
            TTSParams(
                text=args.text,
                ref_audio=ref,
                prompt_text=prompt,
                engine="gptsovits",
            )
        )
        raw_seconds = time.time() - started

        wav, duration = normalize_wav(audio)
    except VoiceError as e:
        print(f"！合成失败：{e}")
        return 1
    finally:
        await engine_obj.close()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(wav)

    print("\n=== 结果")
    print(f"  原始返回 {len(audio) / 1024:.0f} KB，归一化后 {len(wav) / 1024:.0f} KB")
    print(f"  时长 {duration:.2f}s，合成耗时 {raw_seconds:.2f}s")
    print(f"  文件 {out}")

    if duration < 0.3:
        print("！音频太短，像是失败了")
        return 1
    print("\n语音冒烟通过")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="GPT-SoVITS 语音冒烟")
    parser.add_argument("--url", default="http://127.0.0.1:9880")
    parser.add_argument("--ref", default="", help="参考音 wav 路径")
    parser.add_argument("--prompt", default="", help="参考音对应的文本")
    parser.add_argument("--engine", default="gptsovits")
    parser.add_argument("--text", default="你好呀，我是小助手。今天过得怎么样？")
    parser.add_argument("--out", default=str(ROOT / "data" / "voice-smoke.wav"))
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--from-db", action="store_true", help="从库里取参考音")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
