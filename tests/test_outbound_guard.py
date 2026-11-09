"""全局总开关 / dry-run 必须拦截所有出站路径。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.gateway.manager import GatewayManager
from app.gateway.onebot import OneBotClient, OneBotConfig
from app.models import OutboundMessage
from app.settings import Settings


def test_manager_guard_master_and_dry_run(db):
    st = Settings(db)
    gw = GatewayManager(st)
    ob = SimpleNamespace(chat_id="private:1", text="hi")
    st.set("runtime.master_enabled", True)
    st.set("runtime.dry_run", True)
    assert gw._outbound_guard(ob) is False
    st.set("runtime.dry_run", False)
    assert gw._outbound_guard(ob) is True
    st.set("runtime.master_enabled", False)
    assert gw._outbound_guard(ob) is False


def test_onebot_send_guard_blocks_without_connecting():
    client = OneBotClient(OneBotConfig())
    client.send_guard = lambda ob: False
    r = asyncio.run(client.send(OutboundMessage(chat_id="private:1", text="hi")))
    assert r == {"blocked": True}
