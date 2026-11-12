"""语音端到端冒烟：假 OneBot → 真应用 → 真 GPT-SoVITS → 语音消息。

验证的是整条链路：
    群消息 @bot → 注意筛 → mock 模型出文本 → 语音服务选参考音 → 合成
    → 统一 32k → 发 send_group_msg 的 record 段（base64://）

需要一个能用的 GPT-SoVITS 和一个参考音。

用法：
    python -m scripts.voice_e2e_smoke --ref "C:\\...\\ref.wav" \
        --prompt "参考音频对应的文本" [--url http://127.0.0.1:9880]
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.params  # noqa: E402,F401
from app.db import Database  # noqa: E402
from app.memory.migrate import init_schema  # noqa: E402
from app.settings import Settings  # noqa: E402
from app.voice import upsert_ref  # noqa: E402
from app.web.accounts_api import AccountStore  # noqa: E402
from scripts._procutil import cleanup_dir, kill_tree  # noqa: E402

BOT_QQ = "10021"
USER_QQ = 10001
GROUP_ID = 100001


def event(text: str) -> dict:
    return {
        "post_type": "message",
        "message_type": "group",
        "self_id": int(BOT_QQ),
        "user_id": USER_QQ,
        "group_id": GROUP_ID,
        "time": int(time.time()),
        "sender": {"user_id": USER_QQ, "nickname": "TestUser", "card": "主人"},
        "message": [
            {"type": "at", "data": {"qq": BOT_QQ}},
            {"type": "text", "data": {"text": f" {text}"}},
        ],
    }


def seed(db_path: Path, ws_port: int, ref: str, prompt: str) -> None:
    db = Database(db_path)
    init_schema(db)
    s = Settings(db)
    s.set("gateway.enabled", True)
    s.set("reply.cooldown_sec", 0)
    s.set("reply.max_per_min", 0)
    s.set("memory.distill_enabled", False)
    s.set("voice.enabled", True)
    s.set("voice.trigger_mode", "always")
    s.set("voice.min_chars", 1)
    s.set("voice.max_chars", 80)
    s.set("voice.log_enabled", True)

    upsert_ref(db, {
        "id": "e2e-ref",
        "name": "e2e",
        "engine": "gptsovits",
        "audio_path": ref,
        "prompt_text": prompt,
        "min_chars": 0,
        "max_chars": 999,
    })

    store = AccountStore(db)
    store.create({
        "id": "acc-voice",
        "name": "voice",
        "bot_qq": BOT_QQ,
        "url": f"ws://127.0.0.1:{ws_port}",
        "impl": "snowluma",
        "enabled": True,
        "auto_start": True,
    })
    db.close()


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


async def main() -> int:
    args = parse_args()
    if not Path(args.ref).is_file():
        print(f"！参考音不存在：{args.ref}")
        return 2

    sent: list[dict] = []
    done = asyncio.Event()

    async def handler(ws):
        await asyncio.sleep(0.2)
        await ws.send(json.dumps(event("发条语音听听"), ensure_ascii=False))
        try:
            async with asyncio.timeout(90):
                while True:
                    data = json.loads(await ws.recv())
                    if str(data.get("action", "")).startswith("send_"):
                        await ws.send(json.dumps({
                            "echo": data.get("echo"), "retcode": 0,
                            "data": {"message_id": 1},
                        }))
                        sent.append(data)
                        done.set()
                        return
                    if data.get("echo"):
                        await ws.send(json.dumps({
                            "echo": data.get("echo"), "retcode": 0, "data": {},
                        }))
        except Exception:  # noqa: BLE001
            pass

    server = await websockets.serve(handler, "127.0.0.1", 0)
    ws_port = server.sockets[0].getsockname()[1]

    tmp = Path(tempfile.mkdtemp(prefix="novabot-voice-e2e-"))
    atexit.register(cleanup_dir, tmp)
    db_path = tmp / "e2e.db"
    seed(db_path, ws_port, args.ref, args.prompt)
    http_port = _free_port()

    env = os.environ.copy()
    env.update({
        "NOVABOT_DATABASE__PATH": str(db_path),
        "NOVABOT_LLM__DEFAULT": "mock",
        "NOVABOT_LLM__MOCK__TYPE": "mock",
        "NOVABOT_VOICE__ENABLED": "true",
        "NOVABOT_VOICE__DEFAULT_ENGINE": "gptsovits",
        "NOVABOT_VOICE__ENGINES__GPTSOVITS__TYPE": "gptsovits",
        "NOVABOT_VOICE__ENGINES__GPTSOVITS__URL": args.url,
    })

    print(f"参考音 {args.ref}")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(http_port), "--log-level", "warning"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )

    log = ""
    failure = ""
    try:
        await asyncio.wait_for(done.wait(), timeout=90)
    except Exception as e:  # noqa: BLE001
        failure = f"{type(e).__name__}: {e}"
    finally:
        kill_tree(proc.pid)
        try:
            log, _ = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            log, _ = proc.communicate()
        server.close()
        await server.wait_closed()

    problems: list[str] = []
    if failure:
        problems.append(f"没等到语音消息：{failure}")

    record = None
    if sent:
        segs = sent[0].get("params", {}).get("message") or []
        record = next((s for s in segs if s.get("type") == "record"), None)
    if record is None:
        problems.append("发的不是语音消息（没有 record 段）")
    else:
        raw = record.get("data", {}).get("file", "")
        if not raw.startswith("base64://"):
            problems.append(f"record 段格式不对：{raw[:40]}")
        else:
            data = base64.b64decode(raw[len("base64://"):])
            if not data.startswith(b"RIFF"):
                problems.append("解出来的不是 wav")
            else:
                print(f"\n收到语音：{len(data) / 1024:.0f} KB wav")
                (Path("data") / "voice-e2e.wav").write_bytes(data)
                print("已存到 data/voice-e2e.wav")

    if sent:
        segs = sent[0].get("params", {}).get("message") or []
        kinds = [s.get("type") for s in segs]
        print("消息段：", kinds)

    if problems:
        print("\n！语音端到端失败：")
        for p in problems:
            print(f"  - {p}")
        print("\n--- 日志尾部 ---")
        for line in (log or "").splitlines()[-25:]:
            print(f"  {line}")
        return 1

    print("\n语音端到端冒烟通过")
    return 0


def parse_args():
    parser = argparse.ArgumentParser(description="语音端到端冒烟")
    parser.add_argument("--ref", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:9880")
    return parser.parse_args()


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
