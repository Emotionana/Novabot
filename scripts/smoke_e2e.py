"""端到端冒烟：假 OneBot 服务 + 真应用进程 + 假模型。

和别的冒烟不一样的地方：这条链路是真的——
    真子进程跑 uvicorn
    真 websocket 连上来
    真网关解析事件、跑注意筛、调模型、发 send_group_msg
只是模型换成了 mock，所以确定性、不花钱。

用法：
    python -m scripts.smoke_e2e

它会：
    1. 先建库并把冷却/限流关掉（不然第二条会被冷却挡住）
    2. 起一个假 OneBot（websockets 服务）
    3. 用环境变量把应用配成「连这个假服务 + 用 mock 模型」
    4. 推两条群消息（@bot），等两条 send_group_msg
    5. 通过 /api/data/episodes 查库，确认两轮共 4 条记录
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

import app.params  # noqa: E402,F401  注册参数
from app.db import Database  # noqa: E402
from app.memory.migrate import init_schema  # noqa: E402
from app.settings import Settings  # noqa: E402
from scripts._procutil import cleanup_dir, kill_tree  # noqa: E402

BOT_QQ = "10001"
USER_QQ = "10001"
GROUP_ID = 100001


def event(text: str) -> dict:
    return {
        "post_type": "message",
        "message_type": "group",
        "self_id": int(BOT_QQ),
        "user_id": int(USER_QQ),
        "group_id": GROUP_ID,
        "time": int(time.time()),
        "sender": {"user_id": int(USER_QQ), "nickname": "TestUser", "card": "主人"},
        "message": [
            {"type": "at", "data": {"qq": BOT_QQ}},
            {"type": "text", "data": {"text": f" {text}"}},
        ],
    }


async def collect_until(ws, got: list[dict], want: int, timeout: float = 30.0) -> None:
    """读到 want 条 send_* 为止；其它带 echo 的调用统统回成功。"""
    async with asyncio.timeout(timeout):
        while len(got) < want:
            raw = await ws.recv()
            data = json.loads(raw)
            if str(data.get("action", "")).startswith("send_"):
                await ws.send(
                    json.dumps(
                        {
                            "echo": data.get("echo"),
                            "retcode": 0,
                            "data": {"message_id": len(got) + 1},
                        }
                    )
                )
                got.append(data)
            elif data.get("echo"):
                await ws.send(json.dumps({"echo": data.get("echo"), "retcode": 0, "data": {}}))


def seed_db(db_path: Path) -> None:
    """先把冷却、限流关掉，并关掉蒸馏——冒烟只测对话链路。

    只写全局作用域：账号作用域应该回落到全局（这也是顺带验证的一项）。
    """
    db = Database(db_path)
    init_schema(db)
    settings = Settings(db)
    settings.set("reply.cooldown_sec", 0)
    settings.set("reply.max_per_min", 0)
    settings.set("memory.distill_enabled", False)
    db.close()


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 - 本机地址
        return json.loads(resp.read().decode("utf-8"))


async def main() -> int:
    replies: list[dict] = []
    connected = asyncio.Event()
    done = asyncio.Event()
    first_connection = True

    async def handler(ws):
        """只在第一次连接时推两条消息；重连时挂着别动，避免重复推。"""
        nonlocal first_connection
        connected.set()
        if not first_connection:
            await done.wait()
            return
        first_connection = False
        try:
            await ws.send(json.dumps(event("你好，介绍一下你自己"), ensure_ascii=False))
            await collect_until(ws, replies, 1)
            await ws.send(json.dumps(event("再说一句"), ensure_ascii=False))
            await collect_until(ws, replies, 2)
            await asyncio.sleep(0.5)  # 让应用把回复的 episode 写完
        except Exception:  # noqa: BLE001 - 超时由 main 判定，这里不刷栈
            pass
        await done.wait()

    ws_server = await websockets.serve(handler, "127.0.0.1", 0)
    ws_port = ws_server.sockets[0].getsockname()[1]

    tmp = Path(tempfile.mkdtemp(prefix="novabot-e2e-"))
    atexit.register(cleanup_dir, tmp)
    db_path = tmp / "e2e.db"
    seed_db(db_path)
    http_port = _free_port()

    env = os.environ.copy()
    env.update(
        {
            "NOVABOT_DATABASE__PATH": str(db_path),
            "NOVABOT_LLM__DEFAULT": "mock",
            "NOVABOT_LLM__MOCK__TYPE": "mock",
            "NOVABOT_GATEWAY__ENABLED": "true",
            "NOVABOT_GATEWAY__ACCOUNTS__TEST__BOT_QQ": BOT_QQ,
            "NOVABOT_GATEWAY__ACCOUNTS__TEST__ONEBOT_URL": f"ws://127.0.0.1:{ws_port}",
        }
    )

    print(f"假 OneBot 监听 ws://127.0.0.1:{ws_port}，应用端口 {http_port}")

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(http_port),
            "--log-level",
            "warning",
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    failure = ""
    episodes: list[dict] = []
    api_results: dict[str, str] = {}
    log = ""
    try:
        await asyncio.wait_for(connected.wait(), timeout=30)
        print("应用已连上假 OneBot")

        deadline = time.time() + 40
        while len(replies) < 2 and time.time() < deadline:
            await asyncio.sleep(0.2)
        if len(replies) < 2:
            raise TimeoutError(f"只收到 {len(replies)} 条回复")

        base = f"http://127.0.0.1:{http_port}"
        checks = {
            "/healthz": lambda d: d.get("ok") is True,
            "/api/info": lambda d: bool(d.get("version")),
            "/api/data/stats": lambda d: isinstance(d.get("tables"), dict),
            "/api/data/people": lambda d: d.get("ok") is True,
            "/api/data/facts": lambda d: d.get("ok") is True,
            "/api/data/chats": lambda d: d.get("ok") is True,
            "/api/settings/schema": lambda d: bool(d.get("groups")),
            # 账号配置里的 QQ 应该同步到参数表，界面上才看得到
            "/api/settings/values": lambda d: d.get("values", {}).get("gateway.bot_qq") == BOT_QQ,
            "/api/gateway/status": lambda d: (
                d.get("enabled") is True and (d.get("accounts") or [{}])[0].get("id") == "test"
            ),
            # 两次回复应该记到全局记账器上
            "/api/data/tokens": lambda d: (d.get("summary") or {}).get("calls", 0) >= 2,
        }
        for path, check in checks.items():
            try:
                payload = await asyncio.to_thread(fetch_json, base + path)
                ok = bool(check(payload))
            except Exception as e:  # noqa: BLE001
                ok, payload = False, {"error": str(e)}
            api_results[path] = "ok" if ok else f"FAILED {str(payload)[:120]}"

        episodes = (
            await asyncio.to_thread(
                fetch_json, f"{base}/api/data/episodes?limit=10"
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
        ws_server.close()
        await ws_server.wait_closed()

    print("\n=== 应用发出的消息")
    for i, item in enumerate(replies, 1):
        segs = item.get("params", {}).get("message") or []
        text = "".join(s.get("data", {}).get("text", "") for s in segs if s.get("type") == "text")
        print(
            f"  {i}. {item.get('action')} group={item.get('params', {}).get('group_id')}"
            f" → {text[:60]}"
        )

    print(f"\n=== 库里 {len(episodes)} 条 episode（通过 /api/data/episodes 查的）")
    for row in episodes:
        print(f"  [{row.get('sender')}] {row.get('text', '')[:40]}")

    print(f"\n=== 接口（{len(api_results)} 个）")
    for path, result in api_results.items():
        print(f"  {result:60s} {path}")

    self_rows = [r for r in episodes if r.get("sender") == "self"]
    user_rows = [r for r in episodes if r.get("sender") != "self"]

    problems: list[str] = []
    if failure:
        problems.append(f"等待回复失败：{failure}")
    problems.extend(
        f"接口失败 {path}：{result}" for path, result in api_results.items() if result != "ok"
    )
    if len(replies) != 2:
        problems.append(f"应该有 2 条发送，实际 {len(replies)}")
    if replies:
        text = "".join(
            s.get("data", {}).get("text", "")
            for s in replies[0].get("params", {}).get("message", [])
            if s.get("type") == "text"
        )
        if "[mock]" not in text:
            problems.append(f"回复不是 mock 模型产的：{text[:60]}")
        if "介绍一下你自己" not in text:
            problems.append("回复里没有带上用户的原话")
        if replies[0].get("params", {}).get("group_id") != GROUP_ID:
            problems.append("发错了群号")
    if len(replies) > 1:
        second = "".join(
            s.get("data", {}).get("text", "")
            for s in replies[1].get("params", {}).get("message", [])
            if s.get("type") == "text"
        )
        if "再说一句" not in second:
            problems.append(f"第二条回复对不上第二条消息：{second[:60]}")
    if len(user_rows) != 2:
        problems.append(f"应该记下 2 条用户消息，实际 {len(user_rows)}")
    if len(self_rows) != 2:
        problems.append(f"应该记下 2 条自己的回复，实际 {len(self_rows)}")

    if problems:
        print("\n！端到端冒烟失败：")
        for p in problems:
            print(f"  - {p}")
        print("\n--- 应用日志（尾部）---")
        for line in (log or "").splitlines()[-40:]:
            print(f"  {line}")
        return 1

    print("\n端到端冒烟通过")
    return 0


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
