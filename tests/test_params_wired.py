"""参数接线门禁。

规则：注册一个参数，就必须有代码读它——否则界面上调了不生效，
用户（包括半年后的自己）会以为改了有用。

现在还有 70 个没接上（记忆/情绪/工具/语音这些模块还没通电）。
这份白名单**只许减不许增**：新加的参数没接线，测试直接失败。
"""

from __future__ import annotations

import pathlib
import re

from app.settings import all_params

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"

# 读参数的两种写法都要认：
#   settings.get("xxx")
#   _setting(settings, "xxx", default)
PATTERNS = (
    re.compile(r'settings\.get\(\s*["\']([\w.]+)["\']'),
    re.compile(r'_setting\(\s*[^,]+,\s*["\']([\w.]+)["\']'),
)

# 还没接线的参数。每接上一个就删一行；不接也行，但别再加新的。
KNOWN_UNWIRED = {
    "debug.decision_log",
    "debug.log_retention_days",
    "debug.memory_log",
    "debug.token_log",
    "gateway.heartbeat_sec",
    "gateway.msg_buffer",
    "gateway.onebot_token",
    "gateway.onebot_url",
    "gateway.platform",
    "gateway.reconnect_sec",
    "llm.cheap",
    "llm.history_chars",
    "llm.top_p",
    "memory.emotion_batch",
    "memory.emotion_enabled",
    "memory.emotion_rule_threshold",
    "memory.emotion_window_days",
    "memory.enabled",
    "memory.episode_retention_days",
    "memory.fact_retention_days",
    "memory.facts_limit",
    "memory.graph_enabled",
    "memory.profile_chars",
    "memory.prune_enabled",
    "memory.recall_chars",
    "memory.recent_count",
    "memory.recent_days",
    "memory.vector_enabled",
    "memory.vector_sim_threshold",
    "memory.walk_decay",
    "memory.walk_depth",
    "memory.walk_min_weight",
    "output.clean_markdown",
    "output.filter_words",
    "output.sticker_enabled",
    "output.typing_delay",
    "persona.switch_cooldown",
    "reply.min_gap_sec",
    "reply.max_per_min",
    "reply.owner_max_per_min",
    "security.danger_confirm",
    "security.group_whitelist",
    "security.panel_password",
    "security.panel_remote",
    "security.private_whitelist",
    "security.rate_limit",
    "server.host",
    "server.log_level",
    "server.port",
    "server.timezone",
    "tools.danger_confirm",
    "tools.enabled",
    "tools.enabled_list",
    "tools.lazy_select",
    "tools.max_per_call",
    "tools.timeout_sec",
    "voice.allow_fallback",
    "voice.default_engine",
    "voice.enabled",
    "voice.fallback_engine",
    "voice.log_enabled",
    "voice.max_chars",
    "voice.min_chars",
    "voice.sticky_on_emotion",
    "voice.trigger_mode",
}


def _read_keys() -> set[str]:
    used: set[str] = set()
    for path in APP_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in PATTERNS:
            used |= set(pattern.findall(text))
    return used


def test_no_new_unwired_param():
    missing = sorted(set(all_params()) - _read_keys() - KNOWN_UNWIRED)
    assert not missing, f"这些参数没有任何代码读取，接上或从注册表删掉：{missing}"


def test_allowlist_has_no_ghosts():
    """白名单里不该留已经删掉的参数名。"""
    ghosts = sorted(KNOWN_UNWIRED - set(all_params()))
    assert not ghosts, f"白名单里有已经不存在的参数：{ghosts}"


def test_read_keys_are_registered():
    """代码里读的 key 必须已注册，否则 Settings.get 会抛 KeyError。"""
    unknown = sorted(_read_keys() - set(all_params()))
    assert not unknown, f"代码读了没注册的参数，运行时会 KeyError：{unknown}"
