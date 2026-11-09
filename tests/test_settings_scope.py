"""参数作用域：账号级 > 全局 > 代码默认。

网页面板写的是全局那份，网关里每个账号读的是账号作用域——
没有回落的话，多账号下"界面上改了不生效"，而且很难查。
"""

from __future__ import annotations

from app.settings import Settings, get_param


def test_global_value_applies_to_account(db):
    Settings(db).set("reply.cooldown_sec", 1.5)

    account = Settings(db, account_id="acc")

    assert account.get("reply.cooldown_sec") == 1.5


def test_account_overrides_global(db):
    Settings(db).set("reply.cooldown_sec", 1.5)
    Settings(db, account_id="acc").set("reply.cooldown_sec", 7.0)

    assert Settings(db, account_id="acc").get("reply.cooldown_sec") == 7.0
    assert Settings(db).get("reply.cooldown_sec") == 1.5


def test_reset_falls_back_to_global(db):
    Settings(db).set("reply.cooldown_sec", 1.5)
    account = Settings(db, account_id="acc")
    account.set("reply.cooldown_sec", 7.0)

    account.reset("reply.cooldown_sec")

    assert account.get("reply.cooldown_sec") == 1.5


def test_unset_falls_back_to_default(db):
    account = Settings(db, account_id="acc")

    default = get_param("reply.cooldown_sec").default

    assert account.get("reply.cooldown_sec") == default
