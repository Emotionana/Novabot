"""端到端自检：假 OneBot 私聊 -> 解析 -> 记忆 -> 人格 -> DSH 模型 -> 回复编排。

不往 QQ 发消息，也不碰正在使用的数据库：复制一份临时库，跑完删除。
用法：
    .venv\\Scripts\\python.exe -m scripts.selftest_chat
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import tempfile
import time
from pathlib import Path

import app.params  # noqa: F401  注册参数
from app import config
from app.adapters.llm.factory import build_llm
from app.core.pipeline import Pipeline
from app.db import Database
from app.gateway.bridge import parse_event
from app.memory.system import MemorySystem
from app.persona import PersonaSystem
from app.settings import Settings

DB = Path("data/novabot.db")


def load_accounts(db: Database) -> list[tuple[str, str]]:
    """从数据库读已启用的账号；空库时返回空列表。"""
    rows = db.query(
        "SELECT id, name FROM accounts WHERE id != 'default' AND enabled = 1 "
        "ORDER BY sort_order, created_at"
    )
    return [(str(r["id"]), str(r["name"])) for r in rows]


async def one(db: Database, account: str, bot_name: str) -> dict:
    settings = Settings(db, account_id=account)
    owner = str(settings.get("security.owner_qq") or "")
    if not owner:
        return {"account": account, "ok": False, "error": "没配置 security.owner_qq"}
    bot_qq = str(settings.get("gateway.bot_qq") or "10000")
    event = {
        "post_type": "message", "message_type": "private", "self_id": int(bot_qq),
        "user_id": int(owner), "time": int(time.time()),
        "sender": {"user_id": int(owner), "nickname": "主人", "card": ""},
        "message": [{"type": "text", "data": {"text": f"在吗？用{bot_name}的口吻只回我一句：收到。"}}],
    }
    msg = parse_event(event)
    if msg is None:
        return {"account": account, "ok": False, "error": "OneBot 私聊事件没有解析出来"}
    msg.account_id = account

    memory = MemorySystem(db)
    persona = PersonaSystem(db, settings)
    llm = build_llm(config.get(), settings, account)
    if llm is None:
        return {"account": account, "ok": False, "error": "没有可用模型（检查 DSH Agent 开关/地址）"}
    pipeline = Pipeline(
        settings=settings, memory=memory, persona=persona, llm=llm,
        owner_qq=owner, bot_name=bot_name,
    )
    try:
        result = await pipeline.handle(msg)
    finally:
        await llm.close()
    return {
        "account": account,
        "ok": bool(result.replied and result.text.strip() and not result.error),
        "chat": msg.chat_id,
        "episode": result.episode_id,
        "decision": getattr(result.decision, "reason", ""),
        "error": result.error or "",
        "replied": result.replied,
        "text": result.text,
    }


async def group_one(db: Database, account: str, bot_name: str) -> dict:
    """群里 @ 一次，确认群聊触发条件也能走到回复。"""
    settings = Settings(db, account_id=account)
    owner = str(settings.get("security.owner_qq") or "")
    bot_qq = str(settings.get("gateway.bot_qq") or "10000")
    groups = settings.get("security.group_whitelist") or []
    group = str(groups[0]) if groups else ""
    if not group:
        return {"account": account, "ok": True, "skipped": "没有群白名单，跳过群聊"}
    event = {
        "post_type": "message", "message_type": "group", "self_id": int(bot_qq),
        "user_id": 20002, "group_id": int(group), "time": int(time.time()),
        "sender": {"user_id": 20002, "nickname": "群友", "card": "群里的名字", "role": "member"},
        "message": [
            {"type": "at", "data": {"qq": bot_qq}},
            {"type": "text", "data": {"text": " 在吗？只回一句：收到。"}},
        ],
    }
    msg = parse_event(event)
    if msg is None:
        return {"account": account, "ok": False, "error": "群聊事件没有解析出来"}
    msg.account_id = account

    memory = MemorySystem(db)
    llm = build_llm(config.get(), settings, account)
    if llm is None:
        return {"account": account, "ok": False, "error": "没有可用模型"}
    pipeline = Pipeline(
        settings=settings, memory=memory, persona=PersonaSystem(db, settings), llm=llm,
        owner_qq=owner, bot_name=bot_name,
    )
    try:
        result = await pipeline.handle(msg)
    finally:
        await llm.close()
    return {
        "account": account, "chat": msg.chat_id, "group": group,
        "ok": bool(result.replied and result.text.strip() and not result.error),
        "episode": result.episode_id, "error": result.error or "",
        "replied": result.replied, "text": result.text,
    }


async def run(db_path: Path) -> int:
    db = Database(db_path)
    results = []
    accounts = load_accounts(db)
    if not accounts:
        print("FAIL: 还没有账号，先在网页「接入」里加一个")
        db.close()
        return 1
    try:
        for account, bot_name in accounts:
            result = await one(db, account, bot_name)
            results.append(result)
            print(
                f"{account:14s} {bot_name:6s} replied={result.get('replied')} "
                f"episode={result.get('episode')} text={result.get('text')!r} "
                f"error={result.get('error')!r}"
            )
    finally:
        db.close()
    group_result = await group_one(db, accounts[0][0], accounts[0][1])
    print(
        f"群聊 @ {group_result.get('group', '')}: replied={group_result.get('replied')} "
        f"text={group_result.get('text')!r} error={group_result.get('error')!r}"
    )
    failed = [r for r in results if not r.get("ok")]
    if not group_result.get("ok"):
        failed.append(group_result)
    if failed:
        print("FAIL:", "; ".join(f"{r['account']}: {r.get('error')}" for r in failed))
        return 1
    print(f"PASS: {len(accounts)} 个账号私聊 + 群聊 @ 都走完了 收消息 -> 人格 -> DSH -> 回复")
    return 0


def main() -> int:
    if not DB.exists():
        print("FAIL: 数据库不存在")
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="pb-selftest-"))
    copy = tmp / "selftest.db"
    shutil.copy2(DB, copy)
    try:
        return asyncio.run(run(copy))
    finally:
        for suffix in ("", "-wal", "-shm"):
            with contextlib.suppress(OSError):
                (tmp / ("selftest.db" + suffix)).unlink(missing_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
