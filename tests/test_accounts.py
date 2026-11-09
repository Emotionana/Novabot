"""多开：账号管理与记忆隔离。

这一组测的是"一个进程里跑多个号，彼此不串"：
  - 账号能增删改查、端口自动错开、token 打码不回写
  - 同一个 sender 在两个号下的记忆、历史、上下文互不可见
"""

from __future__ import annotations

import pytest

from app.core.pipeline import Pipeline
from app.web.accounts_api import TOKEN_MASK, AccountStore
from tests.helpers import group_msg

# ---------------------------------------------------------------------------
# 账号存储
# ---------------------------------------------------------------------------


def test_create_and_list(db):
    st = AccountStore(db)

    acc = st.create({"name": "小助手", "bot_qq": "10001", "url": "ws://127.0.0.1:3001"})

    assert acc["id"].startswith("acc-")
    assert acc["name"] == "小助手"
    listed = st.list()
    assert [a["id"] for a in listed] == [acc["id"]]


def test_ports_do_not_collide(db):
    st = AccountStore(db)

    a = st.create({"name": "a", "bot_qq": "1"})
    b = st.create({"name": "b", "bot_qq": "2"})

    assert a["onebot_port"] != b["onebot_port"]
    assert a["snowluma_webui_port"] != b["snowluma_webui_port"]
    assert a["snowluma_novnc_port"] != b["snowluma_novnc_port"]


def test_token_is_masked_and_never_overwritten_by_mask(db):
    st = AccountStore(db)
    acc = st.create({"name": "a", "bot_qq": "1", "token": "secret-token"})

    assert acc["token"] == TOKEN_MASK
    assert acc["token_set"] is True

    # 界面上看到的是打码值，用户改别的字段时原样回传 —— 不能把真 token 覆盖掉
    st.update(acc["id"], {"token": TOKEN_MASK, "name": "改名了"})

    row = db.query_one("SELECT token, name FROM accounts WHERE id = ?", [acc["id"]])
    assert row["token"] == "secret-token"
    assert row["name"] == "改名了"


def test_delete_removes_account_and_settings(db):
    st = AccountStore(db)
    acc = st.create({"name": "a", "bot_qq": "1"})
    db.execute(
        "INSERT INTO settings (account_id, key, value, updated_at) VALUES (?,?,?,?)",
        [acc["id"], "reply.cooldown_sec", "3", 0],
    )

    st.delete(acc["id"])

    assert st.get(acc["id"]) is None
    assert db.scalar("SELECT COUNT(*) FROM settings WHERE account_id = ?", [acc["id"]]) == 0


def test_update_reverse_flag(db):
    st = AccountStore(db)
    acc = st.create({"name": "a", "bot_qq": "1", "reverse": False})

    updated = st.update(acc["id"], {"reverse": True})

    assert updated["reverse"] is True


# ---------------------------------------------------------------------------
# 记忆隔离（同一个 sender，两个账号）
# ---------------------------------------------------------------------------


def _remember(
    mem, account_id: str, text: str, *, sender: str = "111", chat_id: str = "group:1"
) -> int:
    msg = group_msg(text, sender=sender, chat_id=chat_id)
    msg.account_id = account_id
    return mem.remember(msg)


def test_context_does_not_leak_across_accounts(mem):
    _remember(mem, "acc-a", "A 号的暗号是青龙偃月")
    _remember(mem, "acc-b", "B 号的暗号是丈八蛇矛")

    ctx_a = mem.build_context(
        chat_id="group:1", sender_qq="111", text="暗号", account_id="acc-a"
    )
    ctx_b = mem.build_context(
        chat_id="group:1", sender_qq="111", text="暗号", account_id="acc-b"
    )

    assert "青龙偃月" in ctx_a
    assert "丈八蛇矛" not in ctx_a
    assert "丈八蛇矛" in ctx_b
    assert "青龙偃月" not in ctx_b


def test_history_does_not_leak_across_accounts(settings, mem):
    _remember(mem, "acc-a", "A 号说过的一句话")
    _remember(mem, "acc-b", "B 号说过的一句话")

    pipe = Pipeline(settings=settings, memory=mem, llm=None, bot_name="bot")

    hist_a = " ".join(text for _, text in pipe._history("group:1", account_id="acc-a"))
    hist_b = " ".join(text for _, text in pipe._history("group:1", account_id="acc-b"))

    assert "A 号" in hist_a
    assert "B 号" not in hist_a
    assert "B 号" in hist_b
    assert "A 号" not in hist_b


def test_facts_are_account_scoped(db, mem):
    from app.models import Fact

    for acc, text, obj in (
        ("acc-a", "住在杭州", "杭州"),
        ("acc-b", "住在成都", "成都"),
    ):
        mem.cortex.write_fact(
            Fact(
                account_id=acc, subject_id="111", predicate="住在", object=obj,
                text=text, tier=3,
            )
        )

    ctx_a = mem.build_context(
        chat_id="group:1", sender_qq="111", account_id="acc-a"
    )
    ctx_b = mem.build_context(
        chat_id="group:1", sender_qq="111", account_id="acc-b"
    )

    assert "杭州" in ctx_a and "成都" not in ctx_a
    assert "成都" in ctx_b and "杭州" not in ctx_b


@pytest.mark.parametrize("account_id", ["", "acc-a"])
def test_empty_account_still_works(mem, account_id):
    """没传 account_id 的老路径不能崩。"""
    _remember(mem, account_id or "default", "随便说一句")

    ctx = mem.build_context(
        chat_id="group:1", sender_qq="111", account_id=account_id
    )

    assert "随便说一句" in ctx


def test_same_group_two_accounts_get_separate_chat_rows(mem, db):
    """同一个群号，两个号各自一行——标题/消息数不能互相覆盖。"""
    _remember(mem, "acc-a", "A 号在群里说话", chat_id="group:9")
    _remember(mem, "acc-b", "B 号在群里说话", chat_id="group:9")

    rows = db.query(
        "SELECT account_id, msg_count FROM chats WHERE chat_id = ? ORDER BY account_id",
        ["group:9"],
    )

    assert [r["account_id"] for r in rows] == ["acc-a", "acc-b"]
    assert all(r["msg_count"] == 1 for r in rows)


def test_persona_is_per_account(db, settings):
    """每个号可以有自己的角色：人格按 account_id 选，不能所有号共用一张卡。"""
    from app.models import PersonaCard
    from app.persona import PersonaSystem
    from app.settings import Settings

    ps = PersonaSystem(db, settings)
    ps.save_card(PersonaCard(id="p-a", account_id="acc-a", name="小助手", identity="你是 A"))
    ps.save_card(PersonaCard(id="p-b", account_id="acc-b", name="助手乙", identity="你是 B"))

    settings.set("persona.current", "p-a")                      # 全局默认
    Settings(db, account_id="acc-b").set("persona.current", "p-b")  # B 号覆盖

    prompt_a = ps.build_system_prompt(account_id="acc-a")
    prompt_b = ps.build_system_prompt(account_id="acc-b")

    assert "小助手" in prompt_a
    assert "助手乙" in prompt_b


def test_reorder_accounts(db):
    from app.web.accounts_api import AccountStore

    store = AccountStore(db)
    a = store.create({"id": "acc-a", "name": "A", "bot_qq": "1"})
    b = store.create({"id": "acc-b", "name": "B", "bot_qq": "2"})
    store.db.execute(
        "UPDATE accounts SET sort_order = ? WHERE id IN (?, ?)", [9, a["id"], b["id"]]
    )
    for i, acc_id in enumerate(["acc-b", "acc-a"]):
        store.db.execute(
            "UPDATE accounts SET sort_order = ? WHERE id = ?", [i + 1, acc_id]
        )
    rows = store.list()
    assert [r["id"] for r in rows] == ["acc-b", "acc-a"]


def test_new_account_inherits_owner_not_secrets(db):
    from app.settings import Settings
    from app.web.accounts_api import AccountStore

    s = Settings(db)
    s.set("security.owner_qq", "12345")
    s.set("security.panel_password", "super-secret")
    s.set("platform.token", "platform-secret")
    s.set("persona.current", "nana")

    store = AccountStore(db)
    acc = store.create({"id": "acc-x", "name": "X", "bot_qq": "1"})
    rows = {
        r["key"]: r["value"]
        for r in db.query(
            "SELECT key, value FROM settings WHERE account_id = ?", [acc["id"]]
        )
    }
    assert "security.owner_qq" in rows
    assert "security.panel_password" not in rows
    assert "platform.token" not in rows
    assert "persona.current" in rows


def test_default_account_is_protected(db):
    from app.web.accounts_api import AccountStore

    store = AccountStore(db)
    try:
        store.create({"id": "default", "name": "坏"})
    except ValueError:
        pass
    else:
        raise AssertionError("default 不应该能新建")
    try:
        store.delete("default")
    except ValueError:
        pass
    else:
        raise AssertionError("default 不应该能删除")


def test_raw_account_token_is_not_masked(db):
    from app.web.accounts_api import TOKEN_MASK, AccountStore

    st = AccountStore(db)
    acc = st.create({"name": "raw-token", "bot_qq": "10003", "token": "secret-ws-token"})
    assert st.get(acc["id"])["token"] == TOKEN_MASK
    assert st.get(acc["id"])["token_set"] is True
    raw = st.get_raw(acc["id"])
    assert raw["token"] == "secret-ws-token"
