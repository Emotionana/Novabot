"""语音自动匹配冒烟（真引擎 + 多条参考音）。

验证「根据当前情况自动匹配参考音和参数」：
    短句 → 短参考音（seed 42）
    长句 → 长参考音（seed -1）
    生气 → 情绪参考音
并把哪条被选中、参数是什么打印出来。

用临时库，不碰 data/novabot.db。

用法：
    python -m scripts.voice_auto_smoke --url http://127.0.0.1:9880 \
        --ref "C:\\path\\Videos\\ref.wav" \
        --prompt "参考音频对应的文本"
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.params  # noqa: E402,F401
from app.adapters.tts.base import VoiceError  # noqa: E402
from app.adapters.tts.gptsovits import GPTSoVITSEngine  # noqa: E402
from app.adapters.tts.pool import TTSPool  # noqa: E402
from app.db import Database  # noqa: E402
from app.memory.migrate import init_schema  # noqa: E402
from app.settings import Settings  # noqa: E402
from app.voice import VoiceService, upsert_ref  # noqa: E402


class Cfg:
    class voice:  # noqa: N801
        enabled = True
        default_engine = "gptsovits"
        fallback: list = []
        engines: dict = {}
        refs: list = []


def seed(db: Database, ref: str, prompt: str) -> None:
    common = {
        "engine": "gptsovits",
        "audio_path": ref,
        "prompt_text": prompt,
        "weight": 1.0,
    }
    upsert_ref(db, {**common, "id": "short", "name": "短句", "min_chars": 0, "max_chars": 8,
                    "params": {"seed": 42, "top_k": 5}})
    upsert_ref(db, {**common, "id": "long", "name": "长句", "min_chars": 9, "max_chars": 80,
                    "params": {"seed": -1, "top_k": 8, "temperature": 0.9}})
    upsert_ref(db, {**common, "id": "angry", "name": "生气", "min_chars": 0, "max_chars": 80,
                    "emotions": ["anger"], "params": {"seed": -1, "temperature": 1.1}})


async def run(args) -> int:
    if not Path(args.ref).is_file():
        print(f"！参考音不存在：{args.ref}")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="pb-voice-auto-"))
    atexit.register(shutil.rmtree, tmp, True)
    db = Database(tmp / "voice.db")
    init_schema(db)
    settings = Settings(db)
    settings.set("voice.enabled", True)
    settings.set("voice.trigger_mode", "always")
    settings.set("voice.min_chars", 1)
    settings.set("voice.max_chars", 80)
    settings.set("voice.log_enabled", True)

    seed(db, args.ref, args.prompt)
    engine = GPTSoVITSEngine(url=args.url, timeout=args.timeout)
    if not await engine.health():
        print("！引擎没响应")
        return 2

    service = VoiceService(db, None, Cfg(), settings, pool=TTSPool({"gptsovits": engine}))

    cases = [
        ("短句", "嗯。", None),
        ("长句", "今天想跟你说的事情有点多，先从最重要的一件开始讲吧。", None),
        ("生气", "你是不是又在敷衍我。", "anger"),
    ]

    ok = 0
    for label, text, emotion in cases:
        try:
            wav, meta = await service.preview(text=text, emotion=emotion, chat_id=f"smoke-{label}")
        except VoiceError as e:
            print(f"  ！{label} 失败：{e}")
            continue
        print(
            f"  {label:4s} → 参考音「{meta['ref']}」引擎 {meta['engine']} "
            f"{meta['duration']:.2f}s（{meta['reason']}）"
        )
        ok += 1

    print("\n=== 合成日志")
    for row in db.query(
        "SELECT ref_id, engine, text_len, duration, ok FROM voice_logs ORDER BY id"
    ):
        print(f"  {row['ref_id']:6s} {row['text_len']:3d}字 {row['duration']:.2f}s ok={row['ok']}")

    db.close()
    await engine.close()

    if ok < 3:
        print(f"\n！只成功 {ok}/3")
        return 1
    print("\n自动匹配冒烟通过")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="语音自动匹配冒烟")
    parser.add_argument("--url", default="http://127.0.0.1:9880")
    parser.add_argument("--ref", required=True, help="参考音 wav 路径")
    parser.add_argument("--prompt", required=True, help="参考音对应的文本")
    parser.add_argument("--timeout", type=float, default=120.0)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
