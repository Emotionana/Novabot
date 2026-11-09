"""其他 bot 的群消息默认不参与接话。"""

from __future__ import annotations

from app.core.pipeline import Pipeline
from app.models import ChatType, InboundMessage
from app.settings import Settings


def _pipeline(db, account_id="acc-a"):
    st = Settings(db, account_id=account_id)
    p = object.__new__(Pipeline)
    p.settings = st
    p.owner_qq = "10001"
    return p


def test_other_bot_is_detected(db):
    db.execute(
        "INSERT INTO accounts (id, name, bot_qq, bot_uin, enabled, created_at, updated_at, locked) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ["acc-b", "另一个 bot", "20002", "20002", 1, 0, 0, 0],
    )
    p = _pipeline(db)
    msg = InboundMessage(
        account_id="acc-a", chat_id="group:1", chat_type=ChatType.GROUP,
        sender_qq="20002", text="你好",
    )
    assert p._is_other_bot(msg) is True


def test_human_is_not_bot(db):
    p = _pipeline(db)
    msg = InboundMessage(
        account_id="acc-a", chat_id="group:1", chat_type=ChatType.GROUP,
        sender_qq="30003", text="你好",
    )
    assert p._is_other_bot(msg) is False
