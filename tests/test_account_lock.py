"""账号锁定：锁了不能删除。"""

from __future__ import annotations

import pytest

from app.web.accounts_api import AccountStore


def test_locked_account_cannot_be_deleted(db):
    store = AccountStore(db)
    acc = store.create({"name": "测试锁", "bot_qq": "12345678"})
    assert acc["locked"] is False

    store.update(acc["id"], {"locked": True})
    assert store.get(acc["id"])["locked"] is True
    with pytest.raises(ValueError, match="锁定"):
        store.delete(acc["id"])

    store.update(acc["id"], {"locked": False})
    store.delete(acc["id"])
    assert store.get(acc["id"]) is None
