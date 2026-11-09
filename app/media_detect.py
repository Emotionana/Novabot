"""本地视觉模型自动探测。

聊天模型是 DSH Agent 时通常没有 OpenAI 视觉接口，但本机 Ollama 可能已经有
qwen2.5vl 等视觉模型。这里只探测本地服务，不联网下载模型。
"""

from __future__ import annotations

import logging

import httpx

from .settings import Settings

log = logging.getLogger(__name__)

VISION_HINTS = ("vl", "vision", "llava", "minicpm-v", "moondream")


def _pick_vision_model(names: list[str]) -> str:
    candidates = [n for n in names if any(h in n.lower() for h in VISION_HINTS)]
    if not candidates:
        return ""
    # 优先大上下文版本，QQ 图片经常超过 4k tokens
    for n in candidates:
        if "16k" in n.lower() or "32k" in n.lower():
            return n
    return sorted(candidates)[-1]


async def list_local_vision_models() -> list[dict]:
    """列出本机可选的视觉模型，不自动改配置。"""
    found: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(base: str, names: list[str], source: str) -> None:
        for name in names:
            name = str(name or "").strip()
            if not name or not any(h in name.lower() for h in VISION_HINTS):
                continue
            key = (base, name)
            if key in seen:
                continue
            seen.add(key)
            found.append({
                "label": f"{source} · {name}",
                "base_url": base,
                "model": name,
                "source": source,
            })

    try:
        async with httpx.AsyncClient(timeout=2.5) as c:
            r = await c.get("http://127.0.0.1:11434/api/tags")
            if r.status_code == 200:
                add("http://127.0.0.1:11434/v1",
                    [str(m.get("name") or "") for m in (r.json().get("models") or [])], "Ollama")
    except Exception:  # noqa: BLE001
        pass
    for base, source in (("http://127.0.0.1:1234/v1", "LM Studio"), ("http://127.0.0.1:8000/v1", "OpenAI兼容")):
        try:
            async with httpx.AsyncClient(timeout=1.5) as c:
                r = await c.get(f"{base}/models")
                if r.status_code != 200:
                    continue
                names = [str(m.get("id") or "") for m in (r.json().get("data") or [])]
                add(base, names, source)
        except Exception:  # noqa: BLE001
            continue
    found.sort(key=lambda x: (0 if ("16k" in x["model"].lower() or "32k" in x["model"].lower()) else 1, x["label"].lower()))
    return found


async def detect_local_vision() -> tuple[str, str] | None:
    models = await list_local_vision_models()
    if not models:
        return None
    return models[0]["base_url"], models[0]["model"]


async def apply_detected_vision(db) -> dict:
    found = await detect_local_vision()
    if not found:
        return {"ok": False, "error": "没有探测到本地视觉模型"}
    base, model = found
    updated = []
    rows = db.query("SELECT id FROM accounts WHERE enabled = 1 ORDER BY sort_order, id") if db.table_exists("accounts") else []
    for row in rows:
        acc = str(row["id"])
        st = Settings(db, account_id=acc)
        try:
            if not bool(st.get("media.vision_auto")):
                continue
            mode = str(st.get("media.vision_mode") or "chat")
            if mode == "off":
                continue
            if str(st.get("media.vision_base_url") or "").strip() and str(st.get("media.vision_model") or "").strip():
                continue
            # 直连聊天模型若自己就支持图片，不抢它的配置；Agent/DSH 才补本地视觉
            if mode == "chat" and not bool(st.get("llm.agent_enabled")):
                continue
            st.set("media.vision_mode", "custom")
            st.set("media.vision_base_url", base)
            st.set("media.vision_model", model)
            updated.append(acc)
        except Exception as e:  # noqa: BLE001
            log.warning("账号 %s 视觉自动配置失败：%s", acc, e)
    return {"ok": True, "base_url": base, "model": model, "accounts": updated}
