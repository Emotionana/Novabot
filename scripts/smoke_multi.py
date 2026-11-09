"""多开冒烟：两个账号、两个 OneBot、同一个群。

验证三件事：
  1. 两个号各自连自己的 OneBot，消息不串
  2. 同一个群里说话，各自只收到自己那份回复（回复内容对得上）
  3. 记忆按 account_id 隔离（/api/data/episodes?account_id=）

用法：
    python -m scripts.smoke_multi
"""

from __future__ import annotations

import asyncio
import atexit
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
from app.web.accounts_api import AccountStore  # noqa: E402
from scripts._procutil import cleanup_dir, kill_tree  # noqa: E402

GROUP_ID = 573560374
USER_QQ = 10001
ACCOUNTS = [
    ("acc-a", "10011", "A 号你好，我是来找 A 的"),
    ("acc-b", "10012", "B 号你好，我是来找 B 的"),
]


def event(bot_qq: str, text: str) -> dict:
    return {
        "post_type": "message",
        "message_type": "group",
        "self_id": int(bot_qq),
        "user_id": USER_QQ,
        "group_id": GROUP_ID,
        "time": int(time.time()),
        "sender": {"user_id": USER_QQ, "nickname": "TestUser", "card": "主人"},
        "message": [
            {"type": "at", "data": {"qq": bot_qq}},
            {"type": "text", "data": {"text": f" {text}"}},
        ],
    }


def seed(db_path: Path, ports: dict[str, int]) -> None:
    db = Database(db_path)
    init_schema(db)
    settings = Settings(db)
    settings.set("gateway.enabled", True)
    settings.set("reply.cooldown_sec", 0)
    settings.set("reply.max_per_min", 0)
    settings.set("memory.distill_enabled", False)

    store = AccountStore(db)
    for acc_id, bot_qq, _text in ACCOUNTS:
        store.create(
            {
                "id": acc_id,
                "name": acc_id,
                "bot_qq": bot_qq,
                "url": f"ws://127.0.0.1:{ports[acc_id]}",
                "impl": "snowluma",
                "enabled": True,
                "auto_start": True,
            }
        )
    db.close()


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 - 本机地址
        return json.loads(resp.read().decode("utf-8"))


def reply_text(item: dict) -> str:
    return "".join(
        seg.get("data", {}).get("text", "")
        for seg in item.get("params", {}).get("message", [])
        if seg.get("type") == "text"
    )


async def main() -> int:
    done = asyncio.Event()
    replies: dict[str, list[dict]] = {acc: [] for acc, _, _ in ACCOUNTS}
    servers = []
    ports: dict[str, int] = {}

    def make_handler(bot_qq: str, acc_id: str, text: str):
        async def handler(ws):
            await asyncio.sleep(0.2)
            await ws.send(json.dumps(event(bot_qq, text), ensure_ascii=False))
            try:
                async with asyncio.timeout(30):
                    while True:
                        raw = await ws.recv()
                        data = json.loads(raw)
                        if str(data.get("action", "")).startswith("send_"):
                            await ws.send(
                                json.dumps(
                                    {
                                        "echo": data.get("echo"),
                                        "retcode": 0,
                                        "data": {"message_id": 1},
                                    }
                                )
                            )
                            replies[acc_id].append(data)
                            await done.wait()  # 别关连接，否则应用会重连重发
                            return
                        if data.get("echo"):
                            await ws.send(
                                json.dumps(
                                    {"echo": data.get("echo"), "retcode": 0, "data": {}}
                                )
                            )
            except Exception:  # noqa: BLE001 - 超时由 main 判定
                pass

        return handler

    for acc_id, bot_qq, text in ACCOUNTS:
        server = await websockets.serve(make_handler(bot_qq, acc_id, text), "127.0.0.1", 0)
        servers.append(server)
        ports[acc_id] = server.sockets[0].getsockname()[1]

    tmp = Path(tempfile.mkdtemp(prefix="novabot-multi-"))
    atexit.register(cleanup_dir, tmp)
    db_path = tmp / "multi.db"
    seed(db_path, ports)
    http_port = _free_port()

    env = os.environ.copy()
    env.update(
        {
            "NOVABOT_DATABASE__PATH": str(db_path),
            "NOVABOT_LLM__DEFAULT": "mock",
            "NOVABOT_LLM__MOCK__TYPE": "mock",
        }
    )
    print("账号端口:", ports, "应用端口:", http_port)

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(http_port), "--log-level", "warning",
        ],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )

    failure = ""
    episodes: dict[str, list[dict]] = {}
    log = ""
    try:
        deadline = time.time() + 40
        while time.time() < deadline and not all(replies[a] for a in replies):
            await asyncio.sleep(0.3)
        if not all(replies[a] for a in replies):
            raise TimeoutError(f"回复不全：{ {k: len(v) for k, v in replies.items()} }")

        for acc_id, _, _ in ACCOUNTS:
            episodes[acc_id] = (
                await asyncio.to_thread(
                    fetch_json,
                    f"http://127.0.0.1:{http_port}/api/data/episodes"
                    f"?limit=20&account_id={acc_id}",
                )
            ).get("episodes") or []
    except Exception as e:  # noqa: BLE001
        failure = f"{type(e).__name__}: {e}"
    finally:
        done.set()
        kill_tree(proc.pid)
        try:
            log, _ = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            log, _ = proc.communicate()
        for server in servers:
            server.close()
        await asyncio.gather(*(s.wait_closed() for s in servers))

    print("\n=== 两个号各自收到")
    for acc_id, bot_qq, _text in ACCOUNTS:
        got = replies[acc_id]
        print(f"  {acc_id}(bot {bot_qq}) → {reply_text(got[0])[:60] if got else '（没收到）'}")

    print("\n=== 记忆隔离")
    for acc_id, _, _ in ACCOUNTS:
        print(f"  {acc_id}: {[e['text'][:22] for e in episodes.get(acc_id, [])]}")

    problems: list[str] = []
    if failure:
        problems.append(f"等待失败：{failure}")

    for acc_id, _bot_qq, text in ACCOUNTS:
        got = replies[acc_id]
        if len(got) != 1:
            problems.append(f"{acc_id} 应该收到 1 条回复，实际 {len(got)}")
            continue
        if got[0].get("params", {}).get("group_id") != GROUP_ID:
            problems.append(f"{acc_id} 发错群")
        if text[:4] not in reply_text(got[0]):
            problems.append(f"{acc_id} 收到的回复对不上：{reply_text(got[0])[:40]}")

    a_texts = [e["text"] for e in episodes.get("acc-a", [])]
    b_texts = [e["text"] for e in episodes.get("acc-b", [])]
    if not any("A 号" in t for t in a_texts):
        problems.append("acc-a 的记忆里没有 A 号的消息")
    if any("B 号" in t for t in a_texts):
        problems.append(f"acc-a 的记忆里混进了 B 号：{b_texts}")
    if not any("B 号" in t for t in b_texts):
        problems.append("acc-b 的记忆里没有 B 号的消息")
    if any("A 号" in t for t in b_texts):
        problems.append(f"acc-b 的记忆里混进了 A 号：{a_texts}")
    if not any(e["sender"] == "self" for e in episodes.get("acc-a", [])):
        problems.append("acc-a 没有记下自己的回复")
    if not any(e["sender"] == "self" for e in episodes.get("acc-b", [])):
        problems.append("acc-b 没有记下自己的回复")

    if problems:
        print("\n！多开冒烟失败：")
        for p in problems:
            print(f"  - {p}")
        print("\n--- 应用日志（尾部）---")
        for line in (log or "").splitlines()[-30:]:
            print(f"  {line}")
        return 1

    print("\n多开冒烟通过")
    return 0


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
