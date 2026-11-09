"""拉群/推荐联系人确认与白名单同步。"""

from __future__ import annotations

import asyncio

from app.access_service import AccessService
from app.models import ChatType, InboundMessage
from app.permissions import PermissionService
from app.settings import Settings


class FakeClient:
    def __init__(self):
        self.calls = []

    async def call(self, action, params):
        self.calls.append((action, params))
        return {"ok": True}


class FakeLLM:
    async def chat(self, system="", messages=None, **kw):
        from app.adapters.llm.base import Reply

        if "只回答 YES" in system:
            return Reply(text="YES")
        return Reply(text="要同意吗？")


class FakePipeline:
    def __init__(self, db, settings, llm):
        self.permissions = PermissionService(db, settings)
        self.llm = llm


class FakeRT:
    def __init__(self, db, settings, llm):
        self.id = "a1"
        self.client = FakeClient()
        self.pipeline = FakePipeline(db, settings, llm)


class FakeManager:
    def __init__(self, db, settings):
        self.settings = settings
        self.accounts = {}
        self.sent = []

    async def send_to_chat(self, acc_id, chat_id, text):
        self.sent.append((chat_id, text))
        return True


def _msg(text, chat_id="private:10001", sender="10001"):
    return InboundMessage(
        account_id="a1", chat_id=chat_id,
        chat_type=ChatType.PRIVATE if chat_id.startswith("private:") else ChatType.GROUP,
        sender_qq=sender, text=text,
    )


def test_invite_question_then_yes_adds_whitelist(db, settings):
    settings.set("security.owner_qq", "10001")
    manager = FakeManager(db, settings)
    rt = FakeRT(db, settings, FakeLLM())
    manager.accounts["a1"] = rt
    svc = AccessService(manager)

    async def run():
        await svc.handle_event("a1", {
            "post_type": "request", "request_type": "group",
            "group_id": "12345", "user_id": "10001", "flag": "f1",
        })
        assert manager.sent and manager.sent[0][0] == "private:10001"
        assert await svc.handle_message("a1", rt, _msg("好")) is True
        assert rt.client.calls == [("set_group_add_request", {
            "flag": "f1", "sub_type": "invite", "approve": True,
        })]
        assert "12345" in (Settings(db, account_id="a1").get("security.group_whitelist") or [])

    asyncio.run(run())


def test_group_leave_removes_whitelist(db, settings):
    settings.set("security.owner_qq", "10001")
    manager = FakeManager(db, settings)
    rt = FakeRT(db, settings, FakeLLM())
    manager.accounts["a1"] = rt
    Settings(db, account_id="a1").set("security.group_whitelist", ["12345"])
    svc = AccessService(manager)

    asyncio.run(svc.handle_event("a1", {
        "post_type": "notice", "notice_type": "group_decrease",
        "self_id": "99999", "user_id": "99999", "group_id": "12345",
    }))
    assert "12345" not in (Settings(db, account_id="a1").get("security.group_whitelist") or [])
