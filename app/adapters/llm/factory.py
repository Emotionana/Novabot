"""按配置造模型适配器。

模型定义在 `config/local.toml` 的 `[llm.<名字>]` 里；
用哪个由参数的 `llm.default` 决定（空了退回 `config.llm.default`）。

为什么要这一层：
    配置（TOML）说的是"有哪些模型"，参数（settings 表）说的是"用哪个"。
    两件事分开，界面改"用哪个"不用重启，写错名字也能在启动日志里看到。
"""

from __future__ import annotations

import logging

from .openai_compat import create_adapter

log = logging.getLogger(__name__)

# 目前所有实现都走 OpenAI 兼容协议；写错 type 时明确报出来，不要静默
SUPPORTED_TYPES = {"", "openai_compat", "openai", "deepseek", "ollama", "vllm", "qwen", "moonshot",
                   "dsh", "trae", "codebuddy", "agent", "agent_http"}


def build_llm(cfg, settings=None, account_id: str = "", *, model_name: str = ""):
    """造一个适配器。没配或配错返回 None（调用方据此降级，不要抛）。"""
    name = model_name or _setting(settings, "llm.default", "") or getattr(
        getattr(cfg, "llm", None), "default", ""
    )
    if settings is not None:
        try:
            agent_on = bool(settings.get("llm.agent_enabled"))
            endpoint = str(settings.get("llm.agent_endpoint") or "").strip()
            if agent_on and endpoint:
                from .agent import AgentAdapter

                return AgentAdapter(
                    platform=str(settings.get("llm.agent_type") or "dsh"),
                    endpoint=endpoint,
                    token=str(settings.get("llm.agent_token") or ""),
                    timeout=float(settings.get("llm.agent_timeout_sec") or 180),
                    db=getattr(settings, "db", None),
                    settings=settings,
                    name="agent",
                )
        except Exception as e:  # noqa: BLE001
            log.warning("agent 配置读取失败：%s", e)

    if not name:
        log.info("没有指定模型（llm.default 为空），%s 不启用对话", account_id or "全局")
        return None

    models = getattr(getattr(cfg, "llm", None), "models", None) or {}
    spec = models.get(name)
    if not isinstance(spec, dict):
        log.warning("模型「%s」在 config/local.toml 里没定义，跳过（可选：%s）", name, ", ".join(models) or "无")
        return None

    kind = str(spec.get("type") or "").strip().lower()
    if kind in ("dsh", "trae", "codebuddy", "agent", "agent_http"):
        from .agent import AgentAdapter

        endpoint = str(spec.get("endpoint") or spec.get("base_url") or "").strip()
        if not endpoint:
            log.warning("模型「%s」是 agent 类型但没有 endpoint", name)
            return None
        return AgentAdapter(
            platform=kind if kind != "agent_http" else "custom",
            endpoint=endpoint, token=str(spec.get("api_key") or spec.get("token") or ""),
            timeout=float(spec.get("timeout") or 180), max_retries=int(spec.get("max_retries") or 1),
            db=getattr(settings, "db", None), settings=settings, name=name,
        )

    # 假模型：不需要 base_url / model，试用和端到端测试用
    if kind == "mock":
        from .mock import MockAdapter

        log.info("模型「%s」是假模型（mock），不会真的调用", name)
        return MockAdapter(reply=spec.get("reply") or None)

    if kind not in SUPPORTED_TYPES:
        log.warning("模型「%s」的 type=%s 暂不支持", name, kind)
        return None

    base_url = str(spec.get("base_url") or "").strip()
    model = str(spec.get("model") or "").strip()
    if not base_url or not model:
        log.warning("模型「%s」缺 base_url 或 model，跳过", name)
        return None

    timeout = spec.get("timeout")
    if timeout is None:
        timeout = _setting(settings, "llm.timeout_sec", 120)
    retries = spec.get("max_retries")
    if retries is None:
        retries = _setting(settings, "llm.retry", 2)

    log.info("模型「%s」就绪：%s @ %s（%s）", name, model, base_url, account_id or "全局")
    return create_adapter(name, {**spec, "timeout": timeout, "max_retries": retries})


def _setting(settings, key: str, default):
    """读参数；参数没注册时不要炸（测试和外部复用都可能没加载注册表）。"""
    if settings is None:
        return default
    try:
        value = settings.get(key)
    except KeyError:
        return default
    return default if value is None else value
