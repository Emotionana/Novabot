"""参数注册表。

每个可调参数声明一次，界面、配置、校验、文档全从这一处生成。

分两级：
  basic     基础项，界面平铺
  advanced  高级项，默认折叠

参考《后台配置对比与取舍.md》的结论：总量控制在 90 项左右。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Param:
    """一个可调参数。"""

    key: str
    label: str
    kind: str = "str"          # bool / int / float / str / enum / list / multiselect
    default: Any = None
    level: str = "advanced"    # basic / advanced
    group: str = "其他"
    desc: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list[tuple[str, str]] | None = None
    requires: str | None = None   # 依赖另一个参数的 key
    unit: str = ""
    restart: bool = False         # 改了要重启
    secret: bool = False          # 读取时打码（token/密码）
    hidden: bool = False          # 不在通用设置页展示，由专用页面控制
    order: int = 0                # 同一分组内的展示顺序，小的在前

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "default": self.default,
            "level": self.level,
            "group": self.group,
            "desc": self.desc,
            "min": self.min,
            "max": self.max,
            "step": self.step,
            "options": self.options,
            "requires": self.requires,
            "unit": self.unit,
            "restart": self.restart,
            "secret": self.secret,
            "hidden": self.hidden,
        }


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, Param] = {}

# 全局版本号：任何实例写了库，其他实例的缓存就作废
_VERSION = 0
SECRET_MASK = "••••••"


def register(p: Param) -> Param:
    if p.key in _REGISTRY:
        raise ValueError(f"参数重复注册：{p.key}")
    _REGISTRY[p.key] = p
    return p


def defparam(
    key: str,
    label: str,
    kind: str = "str",
    default: Any = None,
    *,
    level: str = "advanced",
    group: str = "其他",
    desc: str = "",
    **kw,
) -> Param:
    """快捷声明。"""
    return register(Param(key, label, kind, default, level=level, group=group, desc=desc, **kw))


def get_param(key: str) -> Param | None:
    return _REGISTRY.get(key)


def all_params() -> dict[str, Param]:
    return dict(_REGISTRY)


def by_group() -> dict[str, list[Param]]:
    out: dict[str, list[Param]] = {}
    for p in _REGISTRY.values():
        out.setdefault(p.group, []).append(p)
    for params in out.values():
        params.sort(key=lambda x: (x.level != "basic", x.order, x.key))
    return out


def defaults() -> dict[str, Any]:
    return {k: p.default for k, p in _REGISTRY.items()}


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def validate(p: Param, value: Any) -> Any:
    """类型 + 范围校验，返回规范化后的值。"""
    if p.kind == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    if p.kind == "int":
        v = int(value)
        if p.min is not None:
            v = max(v, int(p.min))
        if p.max is not None:
            v = min(v, int(p.max))
        return v

    if p.kind == "float":
        v = float(value)
        if p.min is not None:
            v = max(v, p.min)
        if p.max is not None:
            v = min(v, p.max)
        return round(v, 6)

    if p.kind == "enum":
        allowed = [o[0] for o in (p.options or [])]
        if value not in allowed:
            raise ValueError(f"{p.label} 只能是 {allowed}，收到 {value!r}")
        return value

    if p.kind == "list":
        if isinstance(value, str):
            return [x.strip() for x in value.split(",") if x.strip()]
        return [str(x) for x in value]

    if p.kind == "multiselect":
        allowed = [o[0] for o in (p.options or [])]
        vals = value if isinstance(value, (list, tuple)) else [value]
        return [x for x in vals if x in allowed]

    return str(value)


# ---------------------------------------------------------------------------
# 设置存储：默认值 → 配置文件 → 数据库
# ---------------------------------------------------------------------------


class Settings:
    """三层覆盖：代码默认值 < 配置文件 < 数据库。"""

    def __init__(self, db, *, account_id: str = ""):
        self.db = db
        self.account_id = account_id
        self._version = _VERSION
        self._cache: dict[str, Any] = {}
        self._ensure_table()

    def _ensure_table(self) -> None:
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
              account_id TEXT NOT NULL DEFAULT '',
              key        TEXT NOT NULL,
              value      TEXT NOT NULL,
              updated_at REAL NOT NULL,
              PRIMARY KEY (account_id, key)
            )
            """
        )

    # -- 读 --------------------------------------------------------------

    def _sync_version(self) -> None:
        global _VERSION
        if self._version != _VERSION:
            self._cache.clear()
            self._version = _VERSION

    def get(self, key: str, default: Any = None) -> Any:
        self._sync_version()
        param = get_param(key)
        if param is None:
            raise KeyError(f"未知参数：{key}")

        ck = f"{self.account_id}:{key}"
        if ck in self._cache:
            return self._cache[ck]

        # 先找本账号的，再回落到全局（account_id = ''）。
        # 网页面板改的是全局那份；没有回落的话，多账号下界面改了等于没改。
        row = self.db.query_one(
            "SELECT value FROM settings WHERE account_id = ? AND key = ?",
            [self.account_id, key],
        )
        if row is None and self.account_id:
            row = self.db.query_one(
                "SELECT value FROM settings WHERE account_id = '' AND key = ?",
                [key],
            )
        if row:
            try:
                val = json.loads(row["value"])
            except json.JSONDecodeError:
                val = param.default
        else:
            val = param.default

        self._cache[ck] = val
        return val

    def get_all(self, *, level: str | None = None) -> dict[str, Any]:
        """取全部参数值。"""
        out: dict[str, Any] = {}
        for key, p in _REGISTRY.items():
            if level and p.level != level:
                continue
            value = self.get(key)
            if p.secret and value:
                value = SECRET_MASK
            out[key] = value
        return out

    # -- 写 --------------------------------------------------------------

    def set(self, key: str, value: Any) -> Any:
        param = get_param(key)
        if param is None:
            raise KeyError(f"未知参数：{key}")
        # 界面上看到的是打码值，原样提交回来时不能把真 token 覆盖掉
        if param.secret and value == SECRET_MASK:
            return self.get(key)
        v = validate(param, value)
        self.db.execute(
            "INSERT OR REPLACE INTO settings (account_id, key, value, updated_at) "
            "VALUES (?,?,?,?)",
            [self.account_id, key, json.dumps(v, ensure_ascii=False), time.time()],
        )
        global _VERSION
        _VERSION += 1
        self._version = _VERSION
        self._cache.pop(f"{self.account_id}:{key}", None)
        return v

    def set_many(self, values: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for k, v in values.items():
            try:
                out[k] = self.set(k, v)
            except (KeyError, ValueError) as e:
                log.warning("设置 %s 失败：%s", k, e)
        return out

    def reset(self, key: str | None = None) -> None:
        global _VERSION
        _VERSION += 1
        self._version = _VERSION
        """恢复默认（删掉数据库里的覆盖）。"""
        if key:
            self.db.execute(
                "DELETE FROM settings WHERE account_id = ? AND key = ?",
                [self.account_id, key],
            )
            self._cache.pop(f"{self.account_id}:{key}", None)
        else:
            self.db.execute("DELETE FROM settings WHERE account_id = ?", [self.account_id])
            self._cache.clear()

    def invalidate(self) -> None:
        """清掉缓存。多进程/多实例场景下，别的进程改了库，
        这里调一下才能看到新值。"""
        self._cache.clear()

    def is_custom(self, key: str) -> bool:
        """这个参数被改过吗。"""
        row = self.db.query_one(
            "SELECT 1 FROM settings WHERE account_id = ? AND key = ?",
            [self.account_id, key],
        )
        return row is not None
