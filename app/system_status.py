"""各模块自检：能跑、有没有接上、有多少数据。

只读，不改库。给 /api/system/modules 和 scripts/check_modules.py 共用。
"""

from __future__ import annotations

import contextlib
import logging

from .memory.migrate import table_counts
from .settings import Settings

log = logging.getLogger(__name__)


def _on(settings, key: str, default: bool = True) -> bool:
    try:
        return bool(settings.get(key))
    except Exception:  # noqa: BLE001
        return default


def _on_any(db, settings, key: str, default: bool = False) -> bool:
    """任一账号开着就算开。

    这些开关在多账号下各存一份，全局那份经常是空的——
    只读全局会把「三个号都开着」误报成「关着」。
    """
    if _on(settings, key, default):
        return True
    try:
        row = db.query_one(
            "SELECT 1 FROM settings WHERE key = ? AND value = ? LIMIT 1",
            [key, "true"],
        )
        return row is not None
    except Exception:  # noqa: BLE001
        return default


def collect(db, account_id: str = "") -> list[dict]:
    counts = table_counts(db)
    settings = Settings(db, account_id=account_id) if account_id else Settings(db)
    rows: list[dict] = []

    def on(key: str, default: bool = False) -> bool:
        if account_id:
            return _on(settings, key, default)
        return _on_any(db, settings, key, default)

    def add(mid: str, name: str, status: str, detail: str = "") -> None:
        rows.append({"id": mid, "name": name, "status": status, "detail": detail})

    # --- 记忆 ---
    add("hippocampus", "海马体（对话）", "ok", f"{counts.get('episodes', 0)} 条原话")
    add("cortex", "新皮层（事实/摘要）", "ok",
        f"{counts.get('facts', 0)} 条事实 / {counts.get('gists', 0)} 条摘要")
    if on("memory.emotion_enabled", True):
        add("amygdala", "杏仁核（情绪）", "ok", f"{counts.get('emotions', 0)} 条判定")
    else:
        add("amygdala", "杏仁核（情绪）", "off", "memory.emotion_enabled 关着")
    if on("memory.graph_enabled", True):
        add("graph", "联想图", "ok", f"{counts.get('nodes', 0)} 节点 / {counts.get('edges', 0)} 边")
    else:
        add("graph", "联想图", "off", "memory.graph_enabled 关着")
    add("profile", "人物画像", "ok", f"{counts.get('person_interests', 0)} 个兴趣")
    add("recall", "记忆召回", "ok", "按意图取画像/最近/事实")
    add("coref", "指代消解", "ok" if db.table_exists("pronoun_resolutions") else "missing",
        "群聊「他/她」提示")

    # --- 人格 / 模型 ---
    cards = counts.get("persona_cards", 0)
    add("persona", "人格卡", "ok" if cards else "warn",
        f"{cards} 张卡" if cards else "还没有人格卡")
    try:
        from . import config

        cfg = config.get()
        engines = list(cfg.voice.engines)
    except Exception:  # noqa: BLE001
        cfg, engines = None, []
    agent_on = on("llm.agent_enabled", False)
    agent_endpoint = ""
    with contextlib.suppress(Exception):
        agent_endpoint = str(settings.get("llm.agent_endpoint") or "").strip()
    if agent_on and agent_endpoint:
        llm_name = f"DSH Agent · {agent_endpoint.split('//')[-1]}"
    elif agent_on:
        llm_name = "DSH Agent · 未填地址"
    else:
        llm_name = (getattr(getattr(cfg, "llm", None), "default", "") or "")
    add("llm", "模型", "ok" if llm_name else "off", llm_name or "未配置 llm.default")
    add("tokens", "用量统计", "ok" if db.table_exists("token_usage") else "off",
        f"{counts.get('token_usage', 0)} 条" if db.table_exists("token_usage") else "还没有用量记录")

    # --- 语音 ---
    if engines and on("voice.enabled", False):
        add("voice", "语音", "ok", ", ".join(engines))
    elif engines:
        add("voice", "语音", "off", f"引擎 {len(engines)} 个，开关关着")
    else:
        add("voice", "语音", "off", "没有配引擎")

    # --- 接入 ---
    accounts = counts.get("accounts", 0)
    with contextlib.suppress(Exception):
        row = db.query_one("SELECT COUNT(*) AS n FROM accounts WHERE enabled = 1 AND id != 'default'")
        if row is not None:
            accounts = int(row["n"])
    add("gateway", "QQ 接入", "ok" if accounts else "off", f"{accounts} 个账号")

    # --- 主动 ---
    active = on("proactive.enabled", False)
    add("proactive", "主动私聊", "ok" if active else "off",
        f"{counts.get('voice_refs', 0)} 参考音 / {counts.get('proactive_log', 0)} 条记录"
        if active else "默认关")

    # --- 蒸馏 ---
    if on("memory.distill_enabled", True):
        add("distill", "后台蒸馏", "ok", f"已入库 {counts.get('episodes', 0)} 条，后台慢慢筛")
    else:
        add("distill", "后台蒸馏", "off", "memory.distill_enabled 关着")

    # --- 文档 ---
    if db.table_exists("documents"):
        add("knowledge", "文档知识库", "ok",
            f"{counts.get('documents', 0)} 篇 / {counts.get('doc_chunks', 0)} 片")
    else:
        add("knowledge", "文档知识库", "missing", "documents 表不存在")

    # --- 工具 ---
    if on("tools.enabled", False):
        add("tools", "工具调用", "ok",
            f"{counts.get('notes', 0)} 笔记 / {counts.get('reminders', 0)} 提醒 / "
            f"{counts.get('tool_calls', 0)} 次调用")
    else:
        add("tools", "工具调用", "off", "tools.enabled 关着（省 token）")

    # --- 语音识别 / 向量 ---
    asr_api = False
    asr_local = False
    with contextlib.suppress(Exception):
        asr_api = bool(str(settings.get("media.asr_base_url") or "").strip())
        asr_local = bool(str(settings.get("media.asr_local_python") or "").strip()) and \
            bool(str(settings.get("media.asr_local_script") or "").strip())
    if asr_local:
        add("asr", "语音识别", "ok", "本机 faster-whisper")
    elif asr_api:
        add("asr", "语音识别", "ok", "Whisper 兼容接口")
    else:
        add("asr", "语音识别", "off", "未配置识别模型")
    if on("memory.vector_enabled", False):
        add("vector", "向量检索", "warn", "开关开着，但引擎还没接")
    else:
        add("vector", "向量检索", "off", "未启用")

    return rows


def summary(rows: list[dict]) -> dict:
    return {
        "total": len(rows),
        "ok": sum(1 for r in rows if r["status"] == "ok"),
        "warn": sum(1 for r in rows if r["status"] == "warn"),
        "off": sum(1 for r in rows if r["status"] == "off"),
        "missing": sum(1 for r in rows if r["status"] == "missing"),
    }
