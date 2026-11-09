"""聊天权限档：主人私聊 / 三档 / 默认 / 每聊天覆盖。"""

from __future__ import annotations

import time

from app.models import ChatType, InboundMessage
from app.permissions import PermissionService


def _msg(chat_id="group:1", *, private=False, sender="20002", account="a1"):
    return InboundMessage(
        account_id=account, chat_id=chat_id,
        chat_type=ChatType.PRIVATE if private else ChatType.GROUP,
        sender_qq=sender, text="x", event_at=time.time(),
    )


def test_owner_and_default(db, settings):
    svc = PermissionService(db, settings)
    owner = svc.profile_for(_msg("private:10001", private=True, sender="10001"), "10001")
    assert owner["id"] == "owner" and owner["write"] is True and owner["shell"] is True
    group = svc.profile_for(_msg("group:1", sender="10001"), "10001")
    # 权限跟账号走：主人在群里也是主人档
    assert group["id"] == "owner" and group["read"] is True and group["write"] is True


def test_custom_tier_and_per_chat_override(db, settings):
    svc = PermissionService(db, settings)
    added = svc.add_tier("测试档位")
    assert added["ok"] is True
    tier = added["key"]
    assert svc.update_profile(tier, {"write": True, "file_send": True})["ok"] is True
    key = "a1|group:1"
    assert svc.set_override(key, tier)["ok"] is True
    group = svc.profile_for(_msg("group:1", sender="20002"), "10001")
    assert group["id"] == tier and group["file_send"] is True
    other = svc.add_tier("另一个档位")["key"]
    assert svc.set_override(key, other)["ok"] is True
    assert svc.profile_for(_msg("group:1"), "10001")["id"] == other
    assert svc.set_override(key, "")["ok"] is True
    assert svc.profile_for(_msg("group:1"), "10001")["id"] == "tier1"

def test_account_level_permission_follows_user_across_chats(db, settings):
    svc = PermissionService(db, settings)
    tier = svc.add_tier("账号档位")["key"]
    assert svc.set_user("20002", tier)["ok"] is True
    group = svc.profile_for(_msg("group:1", sender="20002"), "10001")
    private = svc.profile_for(_msg("private:20002", private=True, sender="20002"), "10001")
    assert group["id"] == tier
    assert private["id"] == tier
    assert svc.set_user("20002", "")["ok"] is True


def test_invite_and_recommend_caps_are_owner_only_by_default(db, settings):
    svc = PermissionService(db, settings)
    owner = svc.profile_for(_msg("private:10001", private=True, sender="10001"), "10001")
    tier1 = svc.profile_for(_msg("group:1", sender="20002"), "10001")
    assert owner["invite_group"] is True
    assert owner["recommend_contact"] is True
    assert tier1["invite_group"] is False
    assert tier1["recommend_contact"] is False


def test_admin_profile_blocks_local_file_changes(db, settings):
    svc = PermissionService(db, settings)
    assert svc.set_admin("30003", True)["ok"] is True
    admin = svc.profile_for(_msg("group:1", sender="30003"), "10001")
    assert admin["id"] == "admin"
    assert admin["read"] is False
    assert admin["write"] is False
    assert admin["delete"] is False
    assert admin["shell"] is False
    assert admin["file_send"] is False
    assert admin["group_admin"] is True
    assert admin["sticker_manage"] is True
    assert admin["whitelist"] is True


def test_admin_is_treated_as_private_whitelist(db, settings):
    from app.core.pipeline import Pipeline

    settings.set("chat.private_access", "whitelist")
    svc = PermissionService(db, settings)
    svc.set_admin("30003", True)
    settings.set("security.private_whitelist", [])
    p = Pipeline.__new__(Pipeline)
    p.settings = settings
    p.owner_qq = "10001"
    p.permissions = svc
    assert p._chat_allowed(_msg("private:30003", private=True, sender="30003")) is True


def test_custom_tier_can_be_deleted_and_users_fall_back_to_tier1(db, settings):
    svc = PermissionService(db, settings)
    tier = svc.add_tier("临时档位")["key"]
    svc.set_user("20002", tier)
    assert tier in svc.view()["custom_tiers"]
    assert svc.delete_tier(tier)["ok"] is True
    assert tier not in svc.view()["custom_tiers"]
    assert svc.profile_for(_msg("group:1", sender="20002"), "10001")["id"] == "tier1"


def test_default_tier_can_only_search(db, settings):
    svc = PermissionService(db, settings)
    default = svc.profile_for(_msg("group:1", sender="20002"), "10001")
    assert default["id"] == "tier1"
    assert default["web"] is True
    for cap in ("read", "write", "delete", "shell", "file_send", "song", "avatar",
                "profile", "whitelist", "group_admin", "invite_group",
                "recommend_contact", "sticker_send", "sticker_collect", "sticker_manage"):
        assert default[cap] is False, cap
