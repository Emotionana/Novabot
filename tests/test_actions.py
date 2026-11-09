"""自然语言动作：权限拒绝、白名单、头像前置条件。"""

from __future__ import annotations

import asyncio
import time

from app.actions import ActionRouter
from app.models import ChatType, InboundMessage, MediaItem
from app.settings import Settings


def _msg(text, *, private=True, sender="10001", chat_id="private:10001", group=None):
    return InboundMessage(
        account_id="a1", chat_id=chat_id,
        chat_type=ChatType.PRIVATE if private else ChatType.GROUP,
        group_id=group, sender_qq=sender, text=text, event_at=time.time(),
    )


def test_whitelist_command_owner(db, settings):
    settings.set("security.owner_qq", "10001")
    router = ActionRouter(db, settings)
    r = asyncio.run(router.handle(_msg("把我加进白名单"), is_owner=True))
    assert r is not None and r.handled
    assert "10001" in (Settings(db).get("security.private_whitelist") or [])


def test_group_action_denied_for_default_tier(db, settings):
    settings.set("security.owner_qq", "10001")
    router = ActionRouter(db, settings)
    r = asyncio.run(router.handle(
        _msg("把本群加白名单", private=False, sender="20002", chat_id="group:9", group="9"),
        is_owner=False,
    ))
    assert r is not None
    assert "不允许" in r.text


def test_avatar_without_connection_is_honest(db, settings):
    settings.set("security.owner_qq", "10001")
    router = ActionRouter(db, settings)
    m = _msg("把头像换成这张")
    m.media = [MediaItem(kind="image", url="https://example.com/a.png")]
    r = asyncio.run(router.handle(m, is_owner=True))
    assert r is not None
    assert "没连上" in r.text or "失败" in r.text


def test_questions_do_not_trigger_actions(db, settings):
    settings.set("security.owner_qq", "10001")
    router = ActionRouter(db, settings)
    for text in ("你叫什么名字", "白名单是什么", "你会翻唱吗", "禁言是什么意思"):
        assert asyncio.run(router.handle(_msg(text), is_owner=True)) is None, text


def test_local_audio_work_is_left_to_dsh(db, settings):
    settings.set("security.owner_qq", "10001")
    router = ActionRouter(db, settings)
    text = (
        "【米提亚角色全语音存档】https://b23.tv/VfOqq4G "
        "用这个作为素材，提取音频，然后降噪，再跑一个米提亚的RVC"
    )
    assert asyncio.run(router.handle(_msg(text), is_owner=True)) is None


class _IntentLLM:
    def __init__(self, answer):
        self.answer = answer

    async def chat(self, **kw):
        from app.adapters.llm.base import Reply
        return Reply(text=self.answer)


def test_bilibili_extract_is_model_gated(db, settings):
    settings.set("security.owner_qq", "10001")
    text = "帮我提取这个 B站视频的音频 https://b23.tv/abc"
    chat_router = ActionRouter(db, settings, llm=_IntentLLM("CHAT"))
    assert asyncio.run(chat_router.handle(_msg(text), is_owner=True)) is None

    action_router = ActionRouter(db, settings, llm=_IntentLLM("ACTION"))
    async def fake_bili(msg, is_owner, text):
        from app.actions import ActionReply
        return ActionReply("开始提取")
    action_router._bili_audio = fake_bili
    result = asyncio.run(action_router.handle(_msg(text), is_owner=True))
    assert result is not None and result.handled


def test_song_command_phrases_are_model_gated(db, settings):
    settings.set("security.owner_qq", "10001")
    action_router = ActionRouter(db, settings, llm=_IntentLLM("ACTION"))
    async def fake_song(msg, is_owner, text):
        from app.actions import ActionReply
        return ActionReply("开始翻唱")
    action_router._song = fake_song
    for text in ("来一首起风了", "唱起风了", "唱一首起风了"):
        r = asyncio.run(action_router.handle(_msg(text), is_owner=True))
        assert r is not None and r.handled, text

    chat_router = ActionRouter(db, settings, llm=_IntentLLM("CHAT"))
    assert asyncio.run(chat_router.handle(_msg("你唱歌好听吗"), is_owner=True)) is None
