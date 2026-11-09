"""多账号隔离：设置、记忆、TTS 参考音互不串。"""

from __future__ import annotations

import time

from app.adapters.tts.selector import VoiceSelector
from app.memory.system import MemorySystem
from app.models import ChatType, InboundMessage
from app.settings import Settings
from app.voice import upsert_ref


def _msg(account, text, chat="private:1", sender="10001"):
    return InboundMessage(
        account_id=account, chat_id=chat, chat_type=ChatType.PRIVATE,
        sender_qq=sender, text=text, event_at=time.time(),
    )


def test_settings_are_per_account(db, settings):
    a = Settings(db, account_id="acc-a")
    a.set("reply.wake_words", ["A"])
    other = Settings(db, account_id="acc-b")
    assert a.get("reply.wake_words") == ["A"]
    assert other.get("reply.wake_words") != ["A"]


def test_memory_does_not_cross_accounts(db):
    mem = MemorySystem(db)
    mem.remember(_msg("acc-a", "SECRET-A 小助手的记忆"))
    mem.remember(_msg("acc-b", "SECRET-B 助手乙的记忆"))
    ctx_b = mem.build_context(chat_id="private:1", sender_qq="10001", text="SECRET-A", account_id="acc-b")
    assert "SECRET-A" not in ctx_b
    assert "SECRET-B" not in ctx_b or "SECRET-A" not in ctx_b
    ctx_a = mem.build_context(chat_id="private:1", sender_qq="10001", text="SECRET-A", account_id="acc-a")
    assert "SECRET-A" in ctx_a


def test_voice_refs_are_per_account(db, settings):
    upsert_ref(db, {
        "id": "ref-a", "account_id": "acc-a", "name": "A 的声音",
        "engine": "fake", "audio_path": "/tmp/a.wav", "min_chars": 0, "max_chars": 999,
    })
    a = VoiceSelector(db, Settings(db, account_id="acc-a"))
    b = VoiceSelector(db, Settings(db, account_id="acc-b"))
    from app.models import RuntimeState
    got_a = a.select("", "c1", "你好", state=RuntimeState())
    got_b = b.select("", "c1", "你好", state=RuntimeState())
    assert got_a.ref is not None and got_a.ref.account_id == "acc-a"
    assert got_b.ref is None
