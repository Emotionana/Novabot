"""隐私隔离：群聊/别的私聊不能读到主人私聊或其他私聊内容。"""

from __future__ import annotations

import asyncio
import time

from app.models import ChatType, InboundMessage
from app.privacy import PrivacyGuard


def _msg(chat_id="group:1", *, private=False, sender="20002", text="", account="a1"):
    return InboundMessage(
        account_id=account, chat_id=chat_id,
        chat_type=ChatType.PRIVATE if private else ChatType.GROUP,
        group_id=None if private else "1",
        sender_qq=sender, sender_nick="测试", text=text,
        event_at=time.time(), recv_at=time.time(),
    )


def seed_conversations(mem):
    mem.hippocampus.write(_msg("private:10001", private=True, sender="10001", text="主人私聊暗号：蓝色月亮计划"))
    mem.hippocampus.write(_msg("private:20002", private=True, sender="20002", text="另一个人的私聊暗号：红色钥匙"))
    mem.hippocampus.write(_msg("group:1", sender="20002", text="群里今天的公开话题是天气"))


def test_group_context_does_not_include_private_chats(mem, settings):
    seed_conversations(mem)
    mem.db.execute(
        "UPDATE people SET style=?, persona=? WHERE qq='20002' AND account_id='a1'",
        ["私聊专属说话方式", "私聊专属性格"],
    )
    guard = PrivacyGuard(mem.db, settings)
    group = _msg("group:1", sender="20002", text="天气")
    policy = guard.policy(group, owner_qq="10001")
    assert "private:10001" not in policy.allowed_chats
    assert "private:20002" not in policy.allowed_chats
    ctx = mem.build_context(
        chat_id="group:1", sender_qq="20002", account_id="a1", text="天气",
        allowed_chat_ids=policy.allowed_chats, profile_detail=policy.profile_detail,
        allow_related=False, allow_documents=False,
    )
    assert "蓝色月亮" not in ctx
    assert "红色钥匙" not in ctx
    assert "私聊专属" not in ctx
    assert "天气" in ctx


def test_owner_private_is_isolated(mem, settings):
    seed_conversations(mem)
    guard = PrivacyGuard(mem.db, settings)
    owner = _msg("private:10001", private=True, sender="10001", text="蓝色月亮")
    policy = guard.policy(owner, owner_qq="10001")
    assert policy.scope == "private_owner"
    assert policy.allowed_chats == {"private:10001"}
    ctx = mem.build_context(
        chat_id="private:10001", sender_qq="10001", account_id="a1", text="蓝色月亮",
        allowed_chat_ids=policy.allowed_chats, profile_detail=False,
        allow_related=False, allow_documents=False, allow_unknown_facts=True,
    )
    assert "蓝色月亮" in ctx
    assert "红色钥匙" not in ctx


def test_output_guard_only_blocks_real_contact_leaks(mem, settings):
    seed_conversations(mem)
    guard = PrivacyGuard(mem.db, settings)
    group = _msg("group:1", sender="20002")
    policy = guard.policy(group, owner_qq="10001")
    # 普通回答不应因为别的私聊里有相似文字就整段拒答
    assert guard.check_output("主人说蓝色月亮计划", group, policy) is None
    # 明确给联系方式才拦
    redacted = guard.check_output("我的手机号是 13800138000", group, policy)
    assert redacted is not None and "13800138000" not in redacted and "……" in redacted
    assert guard.check_output("订单号 13800138000", group, policy) is None
    assert guard.check_output("群里天气不错", group, policy) is None


class RecordingLLM:
    name = "recording"

    def __init__(self):
        self.system = ""
        self.messages = []

    async def chat(self, system="", messages=None, **kw):
        self.system = system
        self.messages = messages or []
        from app.adapters.llm.base import Reply

        return Reply(text="好")

    def capabilities(self):
        from app.adapters.llm.base import Capabilities

        return Capabilities(tools=False, images=False, streaming=False, max_context=8192)


def test_pipeline_prompt_has_privacy_boundary_and_no_other_private_text(mem, settings):
    seed_conversations(mem)
    settings.set("reply.group_mode", "always")
    settings.set("reply.min_gap_sec", 0)
    settings.set("reply.cooldown_sec", 0)
    settings.set("reply.max_per_min", 0)
    llm = RecordingLLM()
    from app.core.pipeline import Pipeline

    p = Pipeline(settings=settings, memory=mem, llm=llm, owner_qq="10001")
    result = asyncio.run(p.handle(_msg("group:1", sender="20002", text="群里天气怎么样")))
    assert result.replied is True
    assert "隐私边界" in llm.system
    assert "不要因为隐私拒绝普通提问" in llm.system
    assert "这个不方便在这里说" not in llm.system
    assert "蓝色月亮" not in llm.system
    assert "红色钥匙" not in llm.system

def test_owner_private_output_is_not_blocked_by_other_chat_text(mem, settings):
    seed_conversations(mem)
    guard = PrivacyGuard(mem.db, settings)
    owner = _msg("private:10001", private=True, sender="10001")
    policy = guard.policy(owner, owner_qq="10001")
    assert policy.scope == "private_owner"
    # 主人私聊允许看主人的资料，不能被“其他聊天原文”误拦
    assert guard.check_output("主人私聊暗号：蓝色月亮计划", owner, policy) is None

def test_group_can_look_up_people_without_reading_other_chats(mem, settings):
    seed_conversations(mem)
    mem.db.execute(
        "UPDATE people SET name=?, alias=?, style=? WHERE qq='20002' AND account_id='a1'",
        ["测试甲", "甲甲", "说话很直接"],
    )
    guard = PrivacyGuard(mem.db, settings)
    group = _msg("group:1", sender="20002", text="这说的是谁")
    policy = guard.policy(group, owner_qq="10001")
    assert policy.profile_detail is True
    ctx = mem.build_context(
        chat_id="group:1", sender_qq="20002", account_id="a1", text="这说的是谁",
        allowed_chat_ids=policy.allowed_chats, profile_detail=True,
        allow_related=False, allow_documents=False,
    )
    assert "甲甲" in ctx
    assert "说话很直接" in ctx
    # 人物资料可以查，但其他私聊的聊天原文仍然不能进来
    assert "蓝色月亮" not in ctx
    assert "红色钥匙" not in ctx
