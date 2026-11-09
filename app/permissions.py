"""聊天级能力权限：主人权限单独、三档自定义、每聊天覆盖、默认档。

只管“能做什么”，不替代文件沙箱；DSH 插件按这里收敛工具。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_PROFILES = {
    "owner": {"name": "主人权限", "read": True, "write": True, "delete": True, "shell": True,
              "file_send": True, "song": True, "avatar": True, "profile": True,
              "whitelist": True, "group_admin": True,
              "invite_group": True, "recommend_contact": True,
              "sticker_send": True, "sticker_collect": True, "sticker_manage": True,
              "web": True},
    "admin": {"name": "管理员权限", "read": False, "write": False, "delete": False, "shell": False,
              "file_send": False, "song": True, "avatar": True, "profile": True,
              "whitelist": True, "group_admin": True,
              "invite_group": True, "recommend_contact": True,
              "sticker_send": True, "sticker_collect": True, "sticker_manage": True,
              "web": True},
    "tier1": {"name": "默认", "read": False, "write": False, "delete": False, "shell": False,
              "file_send": False, "song": False, "avatar": False, "profile": False,
              "whitelist": False, "group_admin": False,
              "invite_group": False, "recommend_contact": False,
              "sticker_send": False, "sticker_collect": False, "sticker_manage": False,
              "web": True},
}

CAP_LABELS = {
    "read": "读取文件",
    "write": "修改/新建文件",
    "delete": "删除文件",
    "shell": "运行命令",
    "file_send": "通过文件模块发送",
    "song": "翻唱",
    "avatar": "修改 QQ 头像",
    "profile": "修改 bot 名称/资料",
    "whitelist": "修改聊天白名单",
    "group_admin": "群管（禁言/踢人/管理）",
    "invite_group": "拉群/退群",
    "recommend_contact": "推荐联系人/删除联系人",
    "sticker_send": "发表情包",
    "sticker_collect": "收藏表情包",
    "sticker_manage": "管理表情库",
    "web": "联网搜索",
}

FIXED_TIERS = ("owner", "admin", "tier1")
# 管理员默认不碰本地机器：读/写/删/跑命令/通过文件模块发本地文件
LOCAL_CAPS = ("read", "write", "delete", "shell", "file_send")
CUSTOM_BASE = dict(DEFAULT_PROFILES["tier1"])


class PermissionService:
    def __init__(self, db, settings):
        self.db = db
        self.settings = settings
        account = getattr(settings, "account_id", "") or ""
        if account:
            safe = re.sub(r"[^\w.-]+", "_", account)[:80] or "default"
            self.file = Path(getattr(db, "path", ".")).parent / "permissions" / f"{safe}.json"
        else:
            self.file = Path(getattr(db, "path", ".")).parent / "permissions.json"
        self.data = self._load()

    def _load(self) -> dict:
        raw = {}
        try:
            raw = json.loads(self.file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            raw = {}
        raw_profiles = raw.get("profiles") if isinstance(raw.get("profiles"), dict) else {}
        profiles = {}
        for key, base in DEFAULT_PROFILES.items():
            saved = raw_profiles.get(key) or {}
            if key == "owner":
                saved_name = "主人权限"
            elif key == "admin":
                saved_name = "管理员权限"
            elif key == "tier1":
                saved_name = "默认"
            else:
                saved_name = str(saved.get("name") or base["name"])
            profiles[key] = {
                "name": saved_name,
                **{cap: bool(saved.get(cap, base[cap])) for cap in base if cap != "name"},
            }

        # 旧配置里的 tier2/tier3 自动迁成自定义档位；新配置默认没有自定义档。
        custom_tiers = [str(x) for x in (raw.get("custom_tiers") or []) if str(x)]
        for key in raw_profiles:
            if key not in FIXED_TIERS and key not in custom_tiers:
                custom_tiers.append(str(key))
        for key in list(custom_tiers):
            if key in FIXED_TIERS:
                custom_tiers.remove(key)
                continue
            saved = raw_profiles.get(key) or {}
            base = CUSTOM_BASE
            profiles[key] = {
                "name": str(saved.get("name") or "自定义档位"),
                **{cap: bool(saved.get(cap, base[cap])) for cap in base if cap != "name"},
            }
        default = "tier1"
        overrides = raw.get("overrides") if isinstance(raw.get("overrides"), dict) else {}
        users = raw.get("users") if isinstance(raw.get("users"), dict) else {}
        users = {str(k): str(v) for k, v in users.items() if str(v) in ("tier1", "tier2", "tier3")}
        admins = raw.get("admins") if isinstance(raw.get("admins"), list) else []
        admins = [str(x).strip() for x in admins if str(x).strip().isdigit()]
        return {"profiles": profiles, "default_tier": default, "overrides": overrides,
                "users": users, "admins": sorted(set(admins)), "custom_tiers": custom_tiers}

    def _save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.file)

    def view(self) -> dict:
        return {
            "ok": True,
            "profiles": self.data["profiles"],
            "default_tier": self.data["default_tier"],
            "overrides": self.data["overrides"],
            "users": self.data.get("users") or {},
            "admins": self.data.get("admins") or [],
            "custom_tiers": self.data.get("custom_tiers") or [],
            "fixed_tiers": list(FIXED_TIERS),
            "owner_qq": str(self.settings.get("security.owner_qq") or ""),
            "cap_labels": CAP_LABELS,
        }

    def update_profile(self, key: str, values: dict) -> dict:
        if key not in self.data["profiles"]:
            return {"ok": False, "error": "没有这个权限档"}
        p = self.data["profiles"][key]
        for cap in CAP_LABELS:
            if cap in values:
                p[cap] = bool(values[cap])
        if values.get("name"):
            p["name"] = str(values["name"])[:40]
        self._save()
        return {"ok": True}

    def set_default(self, tier: str) -> dict:
        if tier != "tier1":
            return {"ok": False, "error": "默认档固定为默认"}
        self.data["default_tier"] = "tier1"
        self._save()
        return {"ok": True}

    def set_override(self, key: str, tier: str = "") -> dict:
        if tier and tier not in self.data["profiles"]:
            return {"ok": False, "error": "权限档不对"}
        if tier:
            self.data["overrides"][key] = tier
        else:
            self.data["overrides"].pop(key, None)
        self._save()
        return {"ok": True}

    def set_user(self, qq: str, tier: str = "") -> dict:
        qq = str(qq or "").strip()
        if not qq:
            return {"ok": False, "error": "QQ 不能为空"}
        if tier and tier not in self.data["profiles"]:
            return {"ok": False, "error": "账号档位不存在"}
        users = self.data.setdefault("users", {})
        if tier:
            users[qq] = tier
        else:
            users.pop(qq, None)
        self._save()
        return {"ok": True}

    def add_tier(self, name: str = "") -> dict:
        custom = self.data.setdefault("custom_tiers", [])
        seq = 1
        while f"tier_custom_{seq}" in self.data["profiles"] or f"tier_custom_{seq}" in custom:
            seq += 1
        key = f"tier_custom_{seq}"
        custom.append(key)
        self.data["profiles"][key] = {
            "name": str(name or f"自定义档位 {seq}")[:40],
            **{cap: bool(CUSTOM_BASE[cap]) for cap in CAP_LABELS},
        }
        self._save()
        return {"ok": True, "key": key, "name": self.data["profiles"][key]["name"]}

    def delete_tier(self, key: str) -> dict:
        if key not in (self.data.get("custom_tiers") or []):
            return {"ok": False, "error": "只能删除自定义档位"}
        self.data["custom_tiers"] = [x for x in self.data["custom_tiers"] if x != key]
        self.data["profiles"].pop(key, None)
        users = self.data.get("users") or {}
        for qq, tier in list(users.items()):
            if tier == key:
                users[qq] = "tier1"
        for chat, tier in list((self.data.get("overrides") or {}).items()):
            if tier == key:
                self.data["overrides"][chat] = "tier1"
        self._save()
        return {"ok": True}

    def set_admin(self, qq: str, enabled: bool = True) -> dict:
        qq = str(qq or "").strip()
        if not qq or not qq.isdigit():
            return {"ok": False, "error": "QQ 必须是数字"}
        admins = self.data.setdefault("admins", [])
        if enabled and qq not in admins:
            admins.append(qq)
        if not enabled:
            self.data["admins"] = [x for x in admins if str(x) != qq]
        self._save()
        return {"ok": True, "admins": self.data["admins"]}

    def is_admin(self, qq: str) -> bool:
        return str(qq or "") in (self.data.get("admins") or [])

    def profile_for(self, msg, owner_qq: str = "") -> dict:
        # 权限跟账号走：主人在群里也保持主人档；普通用户可设账号级档位
        if owner_qq and str(msg.sender_qq) == str(owner_qq):
            key = "owner"
        elif str(msg.sender_qq) in (self.data.get("admins") or []):
            key = "admin"
        else:
            key = (self.data.get("users") or {}).get(str(msg.sender_qq))
            if key not in self.data["profiles"] or key == "owner":
                key = f"{msg.account_id or 'default'}|{msg.chat_id}"
                key = self.data["overrides"].get(key) or self.data["default_tier"]
            if key not in self.data["profiles"] or key == "owner":
                key = "tier1"
        p = dict(self.data["profiles"][key])
        p["id"] = key
        p["prompt"] = self.prompt(key)
        return p

    def prompt(self, key: str) -> str:
        p = self.data["profiles"][key]
        allowed = [CAP_LABELS[c] for c in CAP_LABELS if p.get(c)]
        denied = [CAP_LABELS[c] for c in CAP_LABELS if not p.get(c)]
        actions = [
            label for cap, label in (
                ("song", "翻唱"), ("avatar", "换头像"), ("profile", "改 bot 名称"),
                ("whitelist", "加白名单"), ("group_admin", "群管"),
                ("invite_group", "拉群/退群"), ("recommend_contact", "推荐联系人/删除联系人"),
                ("sticker_send", "发表情包"), ("sticker_collect", "收藏表情"),
            ) if p.get(cap)
        ]
        return (
            f"【当前权限档：{p['name']}】\n"
            f"允许：{'、'.join(allowed) or '无'}。\n"
            f"禁止：{'、'.join(denied) or '无'}。\n"
            f"可用自然语言动作：{'、'.join(actions) or '无'}。\n"
            "超出权限时直接说明做不到，不要尝试绕过；改文件/删文件/跑命令前按 DSH 审批提示确认。"
        )
