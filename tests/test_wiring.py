"""接线测试。

存在的意义：防"写了模块但没人调"这类问题复发。
只测"接上没有"，不测业务正确性。
"""

from __future__ import annotations

import pytest

from app.adapters.llm import factory
from app.adapters.llm.base import Reply
from app.core.tokens import get_meter
from app.gateway import manager as manager_mod
from app.gateway import onebot


class FakeLLM:
    name = "fake"

    def __init__(self, answer: str = "嗯。"):
        self.answer = answer
        self.calls: list[dict] = []
        self.closed = False

    async def chat(self, system="", messages=None, **kw) -> Reply:
        self.calls.append({"system": system, "messages": messages, "kw": kw})
        return Reply(text=self.answer)

    def capabilities(self):
        from app.adapters.llm.base import Capabilities

        return Capabilities()

    async def close(self) -> None:
        self.closed = True


def test_factory_returns_adapter():
    from app import config

    cfg = config.Config()
    cfg.llm.default = "deepseek"
    cfg.llm.models = {
        "deepseek": {"base_url": "http://x/v1", "model": "deepseek-chat", "api_key": "k"}
    }
    llm = factory.build_llm(cfg, None)
    assert llm is not None
    assert llm.name == "deepseek"


def test_factory_returns_none_when_not_configured():
    from app import config

    cfg = config.Config()
    assert factory.build_llm(cfg, None) is None


def test_factory_rejects_incomplete_spec():
    from app import config

    cfg = config.Config()
    cfg.llm.default = "broken"
    cfg.llm.models = {"broken": {"base_url": "", "model": ""}}
    assert factory.build_llm(cfg, None) is None


def test_factory_uses_settings_timeout(settings):
    from app import config

    settings.set("llm.timeout_sec", 33)
    cfg = config.Config()
    cfg.llm.default = "m"
    cfg.llm.models = {"m": {"base_url": "http://x/v1", "model": "m"}}
    llm = factory.build_llm(cfg, settings)
    assert llm is not None
    assert llm.timeout == 33


def test_factory_builds_mock():
    from app import config

    cfg = config.Config()
    cfg.llm.default = "m"
    cfg.llm.models = {"m": {"type": "mock"}}
    llm = factory.build_llm(cfg, None)
    assert llm is not None
    assert llm.name == "mock"


@pytest.mark.asyncio
async def test_mock_adapter_echoes():
    from app.adapters.llm.mock import MockAdapter

    reply = await MockAdapter().chat(messages=[{"role": "user", "content": "你好"}])

    assert reply.ok
    assert "你好" in reply.text


def test_manager_not_active_by_default(settings):
    """网关对象存在不等于接入已开——网页状态栏照着 active 显示。"""
    gm = manager_mod.GatewayManager(settings=settings)

    assert gm.active is False


@pytest.mark.asyncio
async def test_gateway_syncs_bot_qq(monkeypatch, settings):
    """账号配置里的 bot_qq 必须进参数表，否则 @ 不算叫我。"""

    async def noop(self):
        return None

    monkeypatch.setattr(onebot.OneBotClient, "start", noop)

    gm = manager_mod.GatewayManager(settings=settings)
    rt = await gm.add_account("acc3", {"bot_qq": "10002"})

    assert rt.pipeline.settings.get("gateway.bot_qq") == "10002"

    await gm.stop_all()


@pytest.mark.asyncio
async def test_two_accounts_keep_their_own_bot_qq(monkeypatch, settings):
    """第二个账号不能继承第一个的 bot_qq（否则 @ 永远不命中）。"""

    async def noop(self):
        return None

    monkeypatch.setattr(onebot.OneBotClient, "start", noop)

    gm = manager_mod.GatewayManager(settings=settings)
    a = await gm.add_account("acc-a", {"bot_qq": "11111"})
    b = await gm.add_account("acc-b", {"bot_qq": "22222"})

    assert a.pipeline.settings.get("gateway.bot_qq") == "11111"
    assert b.pipeline.settings.get("gateway.bot_qq") == "22222"

    await gm.stop_all()


@pytest.mark.asyncio
async def test_gateway_injects_llm_and_meter(monkeypatch, settings):
    async def noop(self):
        return None

    monkeypatch.setattr(onebot.OneBotClient, "start", noop)

    fake = FakeLLM("好")
    gm = manager_mod.GatewayManager(settings=settings, llm_factory=lambda acc: fake)
    rt = await gm.add_account("acc1", {"bot_qq": "123"})

    assert rt.pipeline.llm is fake
    assert rt.pipeline.meter is get_meter()

    await gm.stop_all()
    assert fake.closed is True, "退出时应该关掉模型连接"


@pytest.mark.asyncio
async def test_gateway_without_llm_still_constructs(monkeypatch, settings):
    async def noop(self):
        return None

    monkeypatch.setattr(onebot.OneBotClient, "start", noop)
    gm = manager_mod.GatewayManager(settings=settings)
    rt = await gm.add_account("acc2", {"bot_qq": "456"})
    assert rt.pipeline.llm is None
    await gm.stop_all()


def test_app_imports(monkeypatch, tmp_path):
    """main.py 能导入并建出 app（不真的起服务）。"""
    monkeypatch.setenv("NOVABOT_DATABASE__PATH", str(tmp_path / "app.db"))
    from app.main import create_app

    app = create_app()
    assert app.title == "Novabot"
