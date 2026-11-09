"""语音引擎的持久化、播种和引擎池构造。

config 里的 `[voice.engines.*]` 只在表为空时播种；之后面板改数据库为准。
"""

from __future__ import annotations

import json
import logging
import re
import time

from .adapters.tts.base import TTSEngine
from .adapters.tts.cosyvoice import CosyVoiceEngine
from .adapters.tts.gptsovits import GPTSoVITSEngine
from .adapters.tts.http import HTTPEngine
from .adapters.tts.openai import OpenAIEngine
from .adapters.tts.pool import TTSPool
from .db import Database

log = logging.getLogger(__name__)

ENGINE_TYPES = {
    "gptsovits": "GPT-SoVITS",
    "cosyvoice": "CosyVoice",
    "openai": "OpenAI 兼容",
    "http": "自定义 HTTP",
}
_SECRET_MASK = "••••••"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_ALIASES = {
    "gpt-sovits": "gptsovits",
    "gpt_sovits": "gptsovits",
    "openai_compat": "openai",
    "openai-compatible": "openai",
    "custom": "http",
}


def normalize_type(value: str) -> str:
    kind = str(value or "gptsovits").strip().lower()
    kind = _ALIASES.get(kind, kind)
    return kind if kind in ENGINE_TYPES else "gptsovits"


def _defaults(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw or "{}")
        return out if isinstance(out, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def public_engine(row) -> dict:
    d = dict(row)
    d["defaults"] = _defaults(d.get("defaults"))
    d["enabled"] = bool(d.get("enabled"))
    d["api_key_set"] = bool(d.get("api_key"))
    d["api_key"] = _SECRET_MASK if d.get("api_key") else ""
    return d


def list_engines(db: Database, account_id: str = "") -> list[dict]:
    if not db.table_exists("voice_engines"):
        return []
    if account_id:
        used = {str(r["engine"]) for r in db.query(
            "SELECT DISTINCT engine FROM voice_refs WHERE account_id = ? AND enabled = 1", [account_id]
        ) if r["engine"]}
        try:
            from .settings import Settings
            default = str(Settings(db, account_id=account_id).get("voice.default_engine") or "")
            if default:
                used.add(default)
        except Exception:  # noqa: BLE001
            pass
        if not used:
            return []
        placeholders = ",".join("?" for _ in used)
        rows = [dict(r) for r in db.query(
            f"SELECT * FROM voice_engines WHERE id IN ({placeholders}) ORDER BY id", sorted(used)
        )]
        try:
            from .settings import Settings
            url = str(Settings(db, account_id=account_id).get("voice.engine_url") or "").strip()
            for row in rows:
                if url and row.get("id") == "gptsovits":
                    row["url"] = url
        except Exception:  # noqa: BLE001
            pass
        return [public_engine(r) for r in rows]
    return [public_engine(r) for r in db.query("SELECT * FROM voice_engines ORDER BY id")]


def get_engine(db: Database, engine_id: str) -> dict | None:
    if not db.table_exists("voice_engines"):
        return None
    row = db.query_one("SELECT * FROM voice_engines WHERE id = ?", [engine_id])
    return public_engine(row) if row else None


def save_engine(db: Database, data: dict) -> dict:
    engine_id = str(data.get("id") or "").strip().lower()
    if not _ID_RE.fullmatch(engine_id):
        raise ValueError("引擎 id 只能用小写字母/数字/下划线/短横线")
    kind = normalize_type(str(data.get("type") or ""))
    name = str(data.get("name") or engine_id).strip() or engine_id
    url = str(data.get("url") or "").strip()
    if not url:
        raise ValueError("必须填服务地址")

    old = db.query_one("SELECT * FROM voice_engines WHERE id = ?", [engine_id])
    api_key = str(data.get("api_key") or "").strip()
    if old is not None and (not api_key or api_key == _SECRET_MASK):
        api_key = old["api_key"] or ""
    defaults = _defaults(data.get("defaults"))
    now = time.time()
    db.execute(
        """
        INSERT INTO voice_engines
          (id, name, type, url, api_key, model, voice, timeout, defaults, enabled, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name, type=excluded.type, url=excluded.url,
          api_key=excluded.api_key, model=excluded.model, voice=excluded.voice,
          timeout=excluded.timeout, defaults=excluded.defaults,
          enabled=excluded.enabled, updated_at=excluded.updated_at
        """,
        [
            engine_id, name, kind, url, api_key,
            str(data.get("model") or ""), str(data.get("voice") or ""),
            float(data.get("timeout") or 120),
            json.dumps(defaults, ensure_ascii=False),
            1 if data.get("enabled", True) else 0,
            now, now,
        ],
    )
    return get_engine(db, engine_id) or {}


def delete_engine(db: Database, engine_id: str) -> bool:
    return db.execute("DELETE FROM voice_engines WHERE id = ?", [engine_id]) > 0


def seed_from_config(db: Database, cfg) -> int:
    """把 config 里的引擎播种进表；已有 id 不覆盖。"""
    if not db.table_exists("voice_engines"):
        return 0
    # 表已有内容就以数据库为准，避免每次启动把用户删掉的引擎/改过的地址又种回来
    if db.query_one("SELECT 1 FROM voice_engines LIMIT 1"):
        return 0
    n = 0
    for name, spec in (getattr(cfg.voice, "engines", None) or {}).items():
        if not isinstance(spec, dict):
            continue
        engine_id = str(name).strip().lower()
        if not _ID_RE.fullmatch(engine_id):
            continue
        if db.query_one("SELECT 1 FROM voice_engines WHERE id = ?", [engine_id]):
            continue
        defaults = {
            k: v for k, v in spec.items()
            if k not in ("type", "url", "api_key", "model", "voice", "timeout")
        }
        now = time.time()
        db.execute(
            "INSERT INTO voice_engines "
            "(id, name, type, url, api_key, model, voice, timeout, defaults, enabled, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                engine_id, engine_id, normalize_type(spec.get("type") or "gptsovits"),
                str(spec.get("url") or ""), str(spec.get("api_key") or ""),
                str(spec.get("model") or ""), str(spec.get("voice") or ""),
                float(spec.get("timeout") or 120),
                json.dumps(defaults, ensure_ascii=False), 1, now, now,
            ],
        )
        n += 1
    return n


def _make_engine(row) -> TTSEngine | None:
    kind = normalize_type(row.get("type") or "")
    url = str(row.get("url") or "")
    timeout = float(row.get("timeout") or 120)
    defaults = _defaults(row.get("defaults"))
    if kind == "gptsovits":
        return GPTSoVITSEngine(url=url, timeout=timeout, defaults=defaults)
    if kind == "cosyvoice":
        return CosyVoiceEngine(url=url, timeout=timeout, defaults=defaults)
    if kind == "openai":
        return OpenAIEngine(
            base_url=url, api_key=str(row.get("api_key") or ""),
            model=str(row.get("model") or ""), voice=str(row.get("voice") or ""),
            timeout=timeout, defaults=defaults,
        )
    if kind == "http":
        return HTTPEngine(url=url, timeout=timeout, defaults=defaults)
    return None


def build_pool_from_db(db: Database, cfg, account_id: str = "") -> TTSPool:
    engines: dict[str, TTSEngine] = {}
    override = ""
    if account_id:
        try:
            from .settings import Settings
            override = str(Settings(db, account_id=account_id).get("voice.engine_url") or "").strip()
        except Exception:  # noqa: BLE001
            override = ""
    if db.table_exists("voice_engines"):
        for row in db.query("SELECT * FROM voice_engines WHERE enabled = 1 ORDER BY id"):
            item = dict(row)
            if override and item.get("id") == "gptsovits":
                item["url"] = override
            engine = _make_engine(item)
            if engine is not None:
                engines[row["id"]] = engine
    if not engines:
        # 表还没播种/测试旧库：退回 config
        for name, spec in (getattr(cfg.voice, "engines", None) or {}).items():
            if not isinstance(spec, dict):
                continue
            row = {
                "type": spec.get("type") or "gptsovits",
                "url": spec.get("url") or "",
                "api_key": spec.get("api_key") or "",
                "model": spec.get("model") or "",
                "voice": spec.get("voice") or "",
                "timeout": spec.get("timeout") or 120,
                "defaults": {k: v for k, v in spec.items()
                             if k not in ("type", "url", "api_key", "model", "voice", "timeout")},
            }
            engine = _make_engine(row)
            if engine is not None:
                engines[str(name)] = engine
    return TTSPool(
        engines,
        fallback=list(getattr(cfg.voice, "fallback", None) or []),
        allow_fallback=bool(getattr(cfg.voice, "allow_fallback", True)),
    )
