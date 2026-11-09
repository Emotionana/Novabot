"""配置加载。

优先级：环境变量 > config/local.toml > config/default.toml

环境变量用 NOVABOT_ 前缀，双下划线表示层级：
  NOVABOT_SERVER__PORT=19101  →  server.port = 19101
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"

DEFAULT_CONFIG = CONFIG_DIR / "default.toml"
LOCAL_CONFIG = CONFIG_DIR / "local.toml"

ENV_PREFIX = "NOVABOT_"


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并，override 优先。"""
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _parse_env() -> dict:
    """从环境变量解析覆盖项。"""
    out: dict = {}
    for key, raw in os.environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        path = key[len(ENV_PREFIX):].lower()
        if not path:
            continue
        parts = path.split("__")
        node = out
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = _coerce(raw)
    return out


def _coerce(raw: str) -> Any:
    """把字符串转成合适的类型。"""
    low = raw.strip().lower()
    if low in ("true", "yes", "on", "1"):
        return True
    if low in ("false", "no", "off", "0"):
        return False
    if low in ("", "none", "null"):
        return None
    try:
        if "." in low:
            return float(low)
        return int(low)
    except ValueError:
        return raw


def _load_toml(path: Path) -> dict:
    """读一个 TOML 文件。

    两件 Windows 上的实务：
      - 记事本 / PowerShell 5 会把文件写成带 BOM 的 UTF-8，tomllib 直接拒收，
        于是整个程序在 import 阶段就崩了。这里剥掉 BOM。
      - 语法错了要说清是哪个文件，别只抛一个 TOMLDecodeError。
    """
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise RuntimeError(f"配置文件解析失败：{path}\n  {e}") from e


def load_raw() -> dict:
    """读配置文件 + 环境变量，返回原始字典。"""
    data: dict = {}
    if DEFAULT_CONFIG.is_file():
        data = _load_toml(DEFAULT_CONFIG)
    if LOCAL_CONFIG.is_file():
        data = _deep_merge(data, _load_toml(LOCAL_CONFIG))
    env = _parse_env()
    if env:
        data = _deep_merge(data, env)
    return data


# ---------------------------------------------------------------------------
# 配置对象
# ---------------------------------------------------------------------------


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 19100
    reload: bool = False
    log_level: str = "info"
    timezone: str = "Asia/Shanghai"   # 时间格式化用，不设就用系统时区


@dataclass
class DatabaseConfig:
    path: str = "data/novabot.db"
    wal: bool = True
    mmap_mb: int = 256
    busy_timeout_ms: int = 5000


@dataclass
class LLMConfig:
    default: str = ""
    fallback: list[str] = field(default_factory=list)
    models: dict[str, dict] = field(default_factory=dict)


@dataclass
class MemoryConfig:
    profile_chars: int = 240
    recall_chars: int = 300
    recent_days: int = 7
    recent_count: int = 5
    facts_limit: int = 6
    episode_retention_days: int = 7
    gist_levels: list[int] = field(default_factory=lambda: [1, 2, 3])
    distill_batch: int = 20
    distill_interval_sec: int = 180
    fact_retention_days: int = 180
    similarity_threshold: float = 0.9
    walk_depth: int = 2
    walk_decay: float = 0.6
    walk_min_weight: float = 0.15


@dataclass
class PersonaConfig:
    default: str = ""
    cards: dict[str, dict] = field(default_factory=dict)


@dataclass
class VoiceConfig:
    enabled: bool = False
    default_engine: str = ""
    fallback: list[str] = field(default_factory=list)
    engines: dict[str, dict] = field(default_factory=dict)
    # [[voice.refs]]：参考音可以写在配置里，启动时同步进库
    refs: list[dict] = field(default_factory=list)


@dataclass
class GatewayConfig:
    enabled: bool = False
    accounts: dict[str, dict] = field(default_factory=dict)


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    persona: PersonaConfig = field(default_factory=PersonaConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    raw: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> Config:
        raw = load_raw()
        cfg = cls(raw=raw)
        if s := raw.get("server"):
            cfg.server = ServerConfig(**{k: v for k, v in s.items() if k in ServerConfig.__annotations__})
        if d := raw.get("database"):
            cfg.database = DatabaseConfig(**{k: v for k, v in d.items() if k in DatabaseConfig.__annotations__})
        if m := raw.get("memory"):
            cfg.memory = MemoryConfig(**{k: v for k, v in m.items() if k in MemoryConfig.__annotations__})
        if llm := raw.get("llm"):
            models = {k: v for k, v in llm.items() if isinstance(v, dict)}
            cfg.llm = LLMConfig(
                default=llm.get("default", ""),
                fallback=llm.get("fallback", []),
                models=models,
            )
        if p := raw.get("persona"):
            # 支持 [persona.cards.xxx]（嵌套）和 [persona.xxx]（平铺）两种写法
            cards = p.get("cards")
            if not isinstance(cards, dict):
                cards = {k: v for k, v in p.items() if isinstance(v, dict)}
            cfg.persona = PersonaConfig(default=p.get("default", ""), cards=cards)
        if v := raw.get("voice"):
            # 支持 [voice.engines.xxx]（嵌套）和 [voice.xxx]（平铺）两种写法
            engines = v.get("engines")
            if not isinstance(engines, dict):
                engines = {k: val for k, val in v.items() if isinstance(val, dict)}
            cfg.voice = VoiceConfig(
                enabled=v.get("enabled", False),
                default_engine=v.get("default_engine", ""),
                fallback=v.get("fallback", []),
                engines=engines,
                refs=[r for r in (v.get("refs") or []) if isinstance(r, dict)],
            )
        if g := raw.get("gateway"):
            # [gateway.accounts.<名字>] 才是账号；不能把 accounts 自己当成一个账号
            accounts = g.get("accounts")
            if not isinstance(accounts, dict):
                accounts = {k: v for k, v in g.items() if isinstance(v, dict)}
            cfg.gateway = GatewayConfig(enabled=bool(g.get("enabled")), accounts=accounts)
        return cfg

    def db_path(self) -> Path:
        p = Path(self.database.path)
        return p if p.is_absolute() else ROOT / p


_cached: Config | None = None


def get() -> Config:
    """取全局配置（首次调用时加载）。"""
    global _cached
    if _cached is None:
        _cached = Config.load()
    return _cached


def reload() -> Config:
    """重新加载（改配置后调用）。"""
    global _cached
    _cached = None
    return get()
