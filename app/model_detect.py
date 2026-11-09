"""模型自动检测。

优先复用已经配置的 DSH Agent 地址，探测 /novabot/api/health；
发现可用就把该地址写入当前账号，避免重启后一直显示“未配置”。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os

import httpx

from .settings import Settings

log = logging.getLogger(__name__)

PROBE_TIMEOUT = 2.0


def agent_chat_url(base: str) -> str:
    base = (base or "").strip().rstrip("/")
    if not base:
        return ""
    if "/novabot/api/" in base:
        return base if base.endswith("/chat") else base + "/chat"
    return base + "/novabot/api/chat"


def agent_health_url(base: str) -> str:
    base = (base or "").strip().rstrip("/")
    if not base:
        return ""
    if "/novabot/api/" in base:
        root = base.split("/novabot/api/", 1)[0]
        return root + "/novabot/api/health"
    return base + "/novabot/api/health"


def _dedupe(values) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip().rstrip("/")
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def candidates(db) -> list[str]:
    values: list[str] = []
    g = Settings(db)
    for key in ("llm.agent_endpoint", "platform.dsh_url"):
        with contextlib.suppress(Exception):
            values.append(str(g.get(key) or ""))
    rows = db.query("SELECT id FROM accounts WHERE enabled = 1 ORDER BY sort_order, id") if db.table_exists("accounts") else []
    for row in rows:
        try:
            st = Settings(db, account_id=row["id"])
            values.append(str(st.get("llm.agent_endpoint") or ""))
            values.append(str(st.get("platform.dsh_url") or ""))
        except Exception:  # noqa: BLE001
            pass
    for env in ("NOVABOT_DSH_URL", "NOVABOT_AGENT_ENDPOINT", "DSH_URL"):
        values.append(os.environ.get(env, ""))
    return _dedupe(agent_chat_url(v) if not str(v).strip().endswith("/chat") else v for v in values if str(v).strip())


async def probe(endpoint: str, *, client: httpx.AsyncClient | None = None) -> bool:
    url = agent_health_url(endpoint)
    if not url:
        return False
    own = client is None
    client = client or httpx.AsyncClient(timeout=PROBE_TIMEOUT)
    try:
        r = await client.get(url)
        if r.status_code != 200:
            return False
        data = r.json()
        return bool(data.get("ok") and data.get("name") == "novabot")
    except Exception:  # noqa: BLE001
        return False
    finally:
        if own:
            await client.aclose()


async def detect_and_apply(db, *, refresh=None) -> dict:
    """探测可用 Agent；成功后写入账号并尝试刷新运行中的 pipeline。"""
    found = ""
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        for endpoint in candidates(db):
            if await probe(endpoint, client=client):
                found = endpoint
                break
    if not found:
        return {"ok": False, "error": "没有探测到可用的 Novabot Agent（DSH）", "endpoint": ""}

    settings = Settings(db)
    with contextlib.suppress(Exception):
        settings.get("llm.agent_auto")
    settings.set("llm.agent_enabled", True)
    settings.set("llm.agent_type", "dsh")
    settings.set("llm.agent_endpoint", found)

    updated = []
    rows = db.query("SELECT id FROM accounts WHERE enabled = 1 ORDER BY sort_order, id") if db.table_exists("accounts") else []
    for row in rows:
        acc = str(row["id"])
        st = Settings(db, account_id=acc)
        try:
            auto = bool(st.get("llm.agent_auto"))
        except Exception:  # noqa: BLE001
            auto = True
        if not auto:
            continue
        # 已经明确配置了别的直连模型时不覆盖；空模型/未配置才自动挂 Agent
        if str(st.get("llm.default") or "").strip() and str(st.get("llm.agent_enabled")):
            continue
        st.set("llm.agent_enabled", True)
        st.set("llm.agent_type", "dsh")
        st.set("llm.agent_endpoint", found)
        updated.append(acc)
    if callable(refresh):
        try:
            await refresh()
        except Exception as e:  # noqa: BLE001
            log.warning("刷新模型连接失败：%s", e)
    return {"ok": True, "endpoint": found, "accounts": updated}


async def probe_loop(db, refresh=None, interval: float = 60.0) -> None:
    """后台每 60 秒探测；DSH 重启或换端口后尽快自动接上。"""
    while True:
        try:
            g = Settings(db)
            auto = bool(g.get("llm.agent_auto"))
            if auto:
                result = await detect_and_apply(db, refresh=refresh)
                if result.get("ok"):
                    log.info("模型自动检测：%s（更新 %s 个账号）", result["endpoint"], len(result["accounts"]))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.debug("模型自动检测失败：%s", e)
        await asyncio.sleep(interval)
