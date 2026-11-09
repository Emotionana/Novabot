"""账号管理。

界面上能加号、删号、改配置。
配置存在数据库的 accounts 表里，重启也在。

每个账号一套：
  独立的 OneBot 连接配置
  独立的参数覆盖（settings 表按 account_id 存）
  独立的记忆（episodes/people/facts 都带 account_id）
"""

from __future__ import annotations

import contextlib
import logging
import re
import time
import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

# 界面上显示的 token 就是这个；回传时不要拿它覆盖真 token
TOKEN_MASK = "••••••"


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------


class AccountIn(BaseModel):
    id: str = ""              # 留空自动生成
    name: str = ""            # 显示名，比如「小助手」
    bot_qq: str = ""
    platform: str = "qq"
    # OneBot 连接
    impl: str = "snowluma"
    reverse: bool = False
    url: str = ""
    listen: str = ""
    token: str = ""
    # 开关
    enabled: bool = True
    auto_start: bool = True
    # 端口（多开时各账号不同）
    onebot_port: int = 0
    snowluma_webui_port: int = 0
    snowluma_novnc_port: int = 0
    locked: bool = False


# ---------------------------------------------------------------------------
# 存储层
# ---------------------------------------------------------------------------


class AccountStore:
    """账号的增删改查。

    存在 accounts 表，配置字段塞到 extra_json 里（表结构没那么多列）。
    """

    def __init__(self, db):
        self.db = db
        with contextlib.suppress(Exception):
            db.execute("ALTER TABLE accounts ADD COLUMN locked INTEGER NOT NULL DEFAULT 0")

    def list(self) -> list[dict]:
        """所有账号。'default' 是系统内部的兜底账号，不显示给用户。"""
        rows = self.db.query(
            "SELECT * FROM accounts WHERE id != 'default' ORDER BY sort_order, created_at"
        )
        return [_row_to_dict(r) for r in rows]

    def get(self, acc_id: str, *, raw: bool = False) -> dict | None:
        row = self.db.query_one("SELECT * FROM accounts WHERE id = ?", [acc_id])
        return _row_to_dict(row, mask_token=not raw) if row else None

    def get_raw(self, acc_id: str) -> dict | None:
        """内部连接用：不返回打码 token。"""
        return self.get(acc_id, raw=True)

    def create(self, data: dict) -> dict:
        """新建账号。id 留空就自动生成。"""
        now = time.time()
        acc_id = str(data.get("id") or f"acc-{uuid.uuid4().hex[:8]}").strip().lower()
        if acc_id == "default":
            raise ValueError("default 是内部账号，不能新建/覆盖")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", acc_id):
            raise ValueError("账号 id 只能用小写字母/数字/短横线")
        name = str(data.get("name") or "").strip()
        bot_qq = str(data.get("bot_qq") or "").strip()
        if not name:
            raise ValueError("名字不能为空")
        if not bot_qq:
            raise ValueError("机器人 QQ 不能为空")

        exists = self.db.query_one("SELECT 1 FROM accounts WHERE id = ?", [acc_id])
        if exists:
            raise ValueError(f"账号 {acc_id} 已存在")

        # 自动分配端口（如果没填）
        ports = self._alloc_ports(data)

        self.db.execute(
            """
            INSERT INTO accounts
              (id, name, platform, bot_qq, nickname, enabled, dry_run,
               created_at, updated_at, impl, reverse_mode, url, listen, token,
               auto_start, onebot_port, snowluma_webui_port, snowluma_novnc_port, sort_order)
            VALUES (?,?,?,?,?,?,?, ?,?,?,?,?,?,?, ?,?,?,?,?)
            """,
            [
                acc_id, name, data.get("platform") or "qq",
                bot_qq, bot_qq,
                1 if data.get("enabled", True) else 0, 0,
                now, now,
                data.get("impl") or "snowluma",
                1 if data.get("reverse") else 0,
                data.get("url") or "", data.get("listen") or "", data.get("token") or "",
                1 if data.get("auto_start", True) else 0,
                ports["onebot"], ports["webui"], ports["novnc"],
                self._next_sort(),
            ],
        )

        # 复制默认账号的参数作为起步（用户后来能改）
        self._inherit_settings(acc_id)

        log.info("新建账号 %s（%s）", acc_id, data.get("name"))
        return self.get(acc_id) or {}

    def update(self, acc_id: str, data: dict) -> dict:
        row = self.db.query_one("SELECT * FROM accounts WHERE id = ?", [acc_id])
        if row is None:
            raise ValueError("账号不存在")

        sets: list[str] = []
        params: list = []

        field_map = {
            "name": "name", "bot_qq": "bot_qq", "platform": "platform",
            "impl": "impl", "url": "url", "listen": "listen", "token": "token",
            "onebot_port": "onebot_port",
            "snowluma_webui_port": "snowluma_webui_port",
            "snowluma_novnc_port": "snowluma_novnc_port",
        }
        for key, col in field_map.items():
            if key not in data or data[key] is None:
                continue
            if key == "token" and data[key] in ("", TOKEN_MASK):
                continue  # 界面显示的是打码值，别覆盖真 token
            sets.append(f"{col} = ?")
            params.append(data[key])

        for key, col in (("reverse", "reverse_mode"), ("enabled", "enabled"),
                         ("auto_start", "auto_start"), ("locked", "locked")):
            if key in data and data[key] is not None:
                sets.append(f"{col} = ?")
                params.append(1 if data[key] else 0)

        if sets:
            sets.append("updated_at = ?")
            params.append(time.time())
            params.append(acc_id)
            self.db.execute(
                f"UPDATE accounts SET {', '.join(sets)} WHERE id = ?", params
            )

        return self.get(acc_id) or {}

    def delete(self, acc_id: str, *, purge_data: bool = False) -> None:
        """删账号。

        purge_data=False 只删账号，聊天记录留着（以后重建能接上）。
        purge_data=True 连数据一起删。
        """
        if acc_id == "default":
            raise ValueError("默认账号不能删除")
        row = self.db.query_one("SELECT locked FROM accounts WHERE id = ?", [acc_id])
        if row and int(row["locked"] or 0):
            raise ValueError("账号已锁定，先解锁再删除")
        self.db.execute("DELETE FROM accounts WHERE id = ?", [acc_id])
        self.db.execute("DELETE FROM settings WHERE account_id = ?", [acc_id])

        if purge_data:
            # emotions 没有 account_id（靠 episodes 级联删）
            for table in ("episodes", "facts", "gists", "people", "nodes", "edges", "chats"):
                with contextlib.suppress(Exception):
                    self.db.execute(f"DELETE FROM {table} WHERE account_id = ?", [acc_id])
            log.warning("账号 %s 的数据已清空", acc_id)

        log.info("删除账号 %s（清数据=%s）", acc_id, purge_data)

    # -- 端口分配 -------------------------------------------------------

    def _alloc_ports(self, data: dict) -> dict:
        """自动分配端口，避免多开撞车。

        OneBot：6700, 6710, 6720...
        WebUI：5100, 5110, 5120...
        noVNC：6100, 6110, 6120...
        """
        idx = self.db.scalar("SELECT COUNT(*) FROM accounts", default=0) or 0

        def pick(base: int, given: int) -> int:
            if given and given > 0:
                return given
            used = {
                r["p"]
                for r in self.db.query(
                    "SELECT onebot_port AS p FROM accounts WHERE onebot_port > 0 "
                    "UNION SELECT snowluma_webui_port FROM accounts WHERE snowluma_webui_port > 0 "
                    "UNION SELECT snowluma_novnc_port FROM accounts WHERE snowluma_novnc_port > 0"
                )
            }
            port = base + idx * 10
            while port in used and port < base + 1000:
                port += 10
            return port

        return {
            "onebot": pick(6700, int(data.get("onebot_port") or 0)),
            "webui": pick(5100, int(data.get("snowluma_webui_port") or 0)),
            "novnc": pick(6100, int(data.get("snowluma_novnc_port") or 0)),
        }

    def _next_sort(self) -> int:
        n = self.db.scalar("SELECT MAX(sort_order) FROM accounts", default=0) or 0
        return int(n) + 1

    # 新账号只继承「主人身份」和人格，不继承密码/令牌/模型/语音。
    # 其它参数靠 Settings.get 回落到全局，全局改了以后新账号也能跟着变。
    INHERIT_KEYS = ("security.owner_qq", "security.owner_name", "persona.current")

    def _inherit_settings(self, acc_id: str) -> None:
        """只把主人身份/人格钉到新账号，绝不复制 secret。"""
        try:
            placeholders = ",".join("?" for _ in self.INHERIT_KEYS)
            rows = self.db.query(
                f"SELECT key, value FROM settings WHERE account_id = '' "
                f"AND key IN ({placeholders})",
                list(self.INHERIT_KEYS),
            )
            for r in rows:
                self.db.execute(
                    "INSERT OR REPLACE INTO settings (account_id, key, value, updated_at) "
                    "VALUES (?,?,?,?)",
                    [acc_id, r["key"], r["value"], time.time()],
                )
        except Exception as e:
            log.debug("继承设置失败（不影响使用）：%s", e)


def _row_to_dict(row, *, mask_token: bool = True) -> dict:
    d = dict(row)
    # 布尔化
    for k in ("enabled", "dry_run", "auto_start", "locked"):
        if k in d:
            d[k] = bool(d[k])
    if "reverse_mode" in d:
        d["reverse"] = bool(d.pop("reverse_mode"))
    # token 打码；内部连接账号时必须拿原值
    if d.get("token"):
        d["token_set"] = True
        if mask_token:
            d["token"] = TOKEN_MASK
    else:
        d["token_set"] = False
        d["token"] = ""
    return d


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


def _store(request: Request) -> AccountStore:
    db = request.app.state.db
    st = getattr(request.app.state, "account_store", None)
    if st is None:
        st = AccountStore(db)
        request.app.state.account_store = st
    return st


class OrderIn(BaseModel):
    ids: list[str] = []


@router.post("/order")
def reorder_accounts(req: OrderIn, request: Request):
    st = _store(request)
    for i, acc_id in enumerate(req.ids):
        st.db.execute(
            "UPDATE accounts SET sort_order = ?, updated_at = ? WHERE id = ? AND id != 'default'",
            [i + 1, time.time(), acc_id],
        )
    return {"ok": True}


@router.get("")
def list_accounts(request: Request):
    """所有账号，带运行状态。"""
    st = _store(request)
    accounts = st.list()

    # 合并运行态
    gw = getattr(request.app.state, "gateway", None)
    runtime = {}
    if gw is not None:
        for item in gw.list_status():
            runtime[item["id"]] = item

    from ..settings import Settings

    for a in accounts:
        rt = runtime.get(a["id"]) or {}
        a["connected"] = rt.get("connected", False)
        a["received"] = rt.get("received", 0)
        a["sent"] = rt.get("sent", 0)
        a["errors"] = rt.get("errors", 0)
        a["last_message_at"] = rt.get("last_message_at", 0)
        a["online"] = a["connected"]
        a["configured"] = bool(a.get("bot_qq") and (a.get("url") or a.get("onebot_port")))
        try:
            a["persona"] = Settings(request.app.state.db, account_id=a["id"]).get("persona.current") or ""
        except Exception:  # noqa: BLE001
            a["persona"] = ""

    return {"ok": True, "accounts": accounts}


@router.get("/{acc_id}")
def get_account(request: Request, acc_id: str):
    st = _store(request)
    a = st.get(acc_id)
    if a is None:
        return {"ok": False, "error": "没有这个账号"}
    return {"ok": True, "account": a}


@router.post("")
async def create_account(req: AccountIn, request: Request):
    st = _store(request)
    try:
        acc = st.create(req.model_dump())
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    # 如果开了自动启动，顺便连上
    gw = getattr(request.app.state, "gateway", None)
    if gw is not None and acc.get("enabled") and acc.get("auto_start"):
        try:
            await gw.add_account(acc["id"], _to_onebot_cfg(st.get_raw(acc["id"]) or acc))
        except Exception as e:
            log.warning("账号 %s 连接失败：%s", acc["id"], e)
            acc["connect_error"] = str(e)[:120]

    return {"ok": True, "account": acc}


@router.patch("/{acc_id}")
async def update_account(request: Request, acc_id: str, req: AccountIn):
    st = _store(request)
    old = st.get(acc_id)
    try:
        acc = st.update(acc_id, req.model_dump(exclude_unset=True))
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    # 只有连接相关字段变了才重连；改名字不该断线
    reconnect = old is None or any(
        old.get(k) != acc.get(k)
        for k in ("impl", "reverse", "url", "token", "listen", "enabled", "auto_start", "onebot_port")
    )
    gw = getattr(request.app.state, "gateway", None)
    if gw is not None and reconnect:
        try:
            await gw.remove_account(acc_id)
            if acc.get("enabled") and acc.get("auto_start"):
                await gw.add_account(acc_id, _to_onebot_cfg(st.get_raw(acc_id) or acc))
        except Exception as e:
            log.warning("账号 %s 重连失败：%s", acc_id, e)

    return {"ok": True, "account": acc}


@router.delete("/{acc_id}")
async def delete_account(request: Request, acc_id: str, purge: bool = False):
    st = _store(request)
    current = st.get(acc_id)
    if current is None:
        return {"ok": False, "error": "账号不存在"}
    if current.get("locked"):
        return {"ok": False, "error": "账号已锁定，先解锁再删除"}

    gw = getattr(request.app.state, "gateway", None)
    if gw is not None:
        with contextlib.suppress(Exception):
            await gw.remove_account(acc_id)

    try:
        st.delete(acc_id, purge_data=purge)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


@router.post("/{acc_id}/connect")
async def connect(request: Request, acc_id: str):
    st = _store(request)
    acc = st.get_raw(acc_id)
    if acc is None:
        return {"ok": False, "error": "没有这个账号"}

    gw = getattr(request.app.state, "gateway", None)
    if gw is None:
        return {"ok": False, "error": "网关没启用"}
    try:
        await gw.add_account(acc_id, _to_onebot_cfg(acc))
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@router.post("/{acc_id}/disconnect")
async def disconnect(request: Request, acc_id: str):
    gw = getattr(request.app.state, "gateway", None)
    if gw is None:
        return {"ok": False, "error": "网关没启用"}
    await gw.remove_account(acc_id)
    return {"ok": True}


def _to_onebot_cfg(acc: dict) -> dict:
    """账号记录 → OneBot 连接配置。"""
    url = acc.get("url") or ""
    if not url and not acc.get("reverse"):
        port = acc.get("onebot_port") or 6700
        url = f"ws://127.0.0.1:{port}"
    return {
        "bot_qq": acc.get("bot_qq") or "",
        "url": url,
        "listen": acc.get("listen") or "",
        "token": acc.get("token") or "",
        "impl": acc.get("impl") or "snowluma",
        "reverse": acc.get("reverse") or False,
    }
