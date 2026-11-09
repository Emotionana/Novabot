"""主动私聊端到端冒烟：假 OneBot → 真应用 → 主动接口 → 私聊消息。

验证的是接线：
    /api/proactive/preview 能算
    /api/proactive/fire 能通过 manager.send_private 发到 OneBot
    /api/proactive/status 能查

不需要真模型，用 mock；不需要真 QQ，用假 OneBot。

用法：
    python -m scripts.smoke_proactive_e2e
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.params  # noqa: E402,F401
from app.db import Database  # noqa: E402
from app.memory.migrate import init_schema  # noqa: E402
from app.settings import Settings  # noqa: E402
from app.web.accounts_api import AccountStore  # noqa: E402
from scripts._procutil import cleanup_dir, kill_tree  # noqa: E402

ACC = "acc-pro"
BOT_QQ = "10031"
USER_QQ = "10001"
GROUP_ID = 0


def seed(db_path: Path, ws_port: int) -> None:
    db = Database(db_path)
    init_schema(db)
    s = Settings(db)
    s.set("gateway.enabled", True)
    s.set("reply.cooldown_sec", 0)
    s.set("reply.max_per_min", 0)
    s.set("memory.distill_enabled", False)
    s.set("proactive.enabled", True)
    s.set("proactive.min_messages", 1)
    s.set("proactive.min_days", 0)
    s.set("proactive.min_silence_hours", 1)
    s.set("proactive.max_per_day", 5)
    s.set("proactive.global_max_per_day", 5)
    s.set("proactive.score_threshold", 0)
    s.set("proactive.quiet_start", 0)
    s.set("proactive.quiet_end", 0)
    s.set("proactive.max_chars", 100)
    s.set("proactive.wait_reply_hours", 24)
    s.set("proactive.interval_hours", 0)

    now = time.time()
    last = now - 3 * 3600
    db.execute(
        "INSERT INTO people (qq, account_id, name, msg_count, first_seen, last_seen, is_owner) "
        "VALUES (?,?,?,?,?,?,?)",
        [USER_QQ, ACC, "老林", 20, now - 20 * 86400, last, 0],
    )
    for i in range(20):
        ts = last - (i % 7) * 86400
        db.execute(
            "INSERT INTO episodes (account_id, chat_id, chat_type, sender_qq, sender_nick, "
            "text, event_at, recv_at) VALUES (?,?,?,?,?,?,?,?)",
            [ACC, f"private:{USER_QQ}", "private", USER_QQ, "老林",
             "晚上好呀" if i % 2 else "今天有点累", ts, ts],
        )

    store = AccountStore(db)
    store.create(
        {
            "id": ACC,
            "name": "pro",
            "bot_qq": BOT_QQ,
            "url": f"ws://127.0.0.1:{ws_port}",
            "impl": "snowluma",
            "enabled": True,
            "auto_start": True,
        }
    )
    db.close()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def main() -> int:
    sent: list[dict] = []
    done = asyncio.Event()

    async def handler(ws):
        try:
            async with asyncio.timeout(60):
                while True:
                    data = json.loads(await ws.recv())
                    action = str(data.get("action", ""))
                    if action.startswith("send_"):
                        sent.append(data)
                        ack = {"echo": data.get("echo"), "retcode": 0,
                               "data": {"message_id": 1}}
                        await ws.send(json.dumps(ack))
                        if action == "send_private_msg":
                            done.set()
                    elif data.get("echo"):
                        await ws.send(json.dumps(
                            {"echo": data.get("echo"), "retcode": 0, "data": {}}
                        ))
        except Exception:  # noqa: BLE001
            pass

    server = await websockets.serve(handler, "127.0.0.1", 0)
    ws_port = server.sockets[0].getsockname()[1]

    tmp = Path(tempfile.mkdtemp(prefix="novabot-proactive-e2e-"))
    atexit.register(cleanup_dir, tmp)
    db_path = tmp / "e2e.db"
    seed(db_path, ws_port)

    http_port = free_port()
    base = f"http://127.0.0.1:{http_port}"
    env = os.environ.copy()
    env.update(
        {
            "NOVABOT_DATABASE__PATH": str(db_path),
            "NOVABOT_LLM__DEFAULT": "mock",
            "NOVABOT_LLM__MOCK__TYPE": "mock",
            "NOVABOT_PROACTIVE__ENABLED": "true",
        }
    )

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(http_port), "--log-level", "warning"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )

    log = ""
    problems: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=70) as c:
            for _ in range(60):
                try:
                    if (await c.get(base + "/healthz")).status_code == 200:
                        break
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("服务没起来")

            status = (await c.get(base + "/api/proactive/status")).json()
            print(f"状态：enabled={status.get('enabled')} 今日={status.get('today_sent')}")
            if not status.get("enabled"):
                problems.append("主动开关没开")

            preview = (await c.post(base + "/api/proactive/preview", json={"account_id": ACC})).json()
            decisions = preview.get("decisions") or []
            print(f"预览：{decisions}")
            if not decisions or decisions[0].get("status") != "preview":
                problems.append("预览没有给出候选")

            fire = (
                await c.post(
                    base + "/api/proactive/fire",
                    json={"account_id": ACC, "qq": USER_QQ, "text": "在忙吗"},
                )
            ).json()
            print(f"手动发送：{fire}")
            if fire.get("status") != "sent":
                problems.append(f"手动发送失败：{fire}")

        try:
            await asyncio.wait_for(done.wait(), timeout=15)
        except Exception as e:  # noqa: BLE001
            problems.append(f"没收到私聊消息：{type(e).__name__}")

        if sent:
            msg = sent[-1]
            print(f"OneBot 收到：{msg.get('action')} {msg.get('params')}")
            if msg.get("action") != "send_private_msg":
                problems.append("发的不是私聊")
            if str(msg.get("params", {}).get("user_id")) != USER_QQ:
                problems.append("收件人不对")
    except Exception as e:  # noqa: BLE001
        problems.append(f"{type(e).__name__}: {e}")
    finally:
        kill_tree(proc.pid)
        try:
            log, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            log, _ = proc.communicate()
        server.close()
        await server.wait_closed()
        cleanup_dir(tmp)

    if problems:
        print("\n！主动私聊端到端失败：")
        for p in problems:
            print(f"  - {p}")
        print("\n--- 日志尾部 ---")
        for line in (log or "").splitlines()[-25:]:
            print(f"  {line}")
        return 1

    print("\n主动私聊端到端冒烟通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
