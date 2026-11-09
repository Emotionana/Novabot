"""DSH 在账号启动后才启用时，pipeline 要能补上模型。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import app.params  # noqa: F401


def test_gateway_attaches_agent_after_settings_enable(db, settings):
    from app.gateway.manager import AccountRuntime, GatewayManager

    settings.set("llm.agent_enabled", True)
    settings.set("llm.agent_type", "dsh")
    settings.set("llm.agent_endpoint", "http://127.0.0.1:64116/novabot/api/chat")

    marker = object()
    gw = GatewayManager(settings, llm_factory=lambda account_id: marker)
    rt = AccountRuntime(id="a1")
    rt.pipeline = SimpleNamespace(llm=None)

    asyncio.run(gw._ensure_llm(rt))

    assert rt.pipeline.llm is marker
