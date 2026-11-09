"""平台探测：DSH 健康时自动写 Agent 配置。"""

from __future__ import annotations

import asyncio

from app import platform_probe


class FakeSettings:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = value


class FakeDB:
    def scalar(self, *a, **k):
        return 0


class FakeResp:
    status_code = 200

    def json(self):
        return {"ok": True}


class FakeClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        return FakeResp()


def test_probe_autoconnects_dsh(monkeypatch):
    monkeypatch.setattr(platform_probe.httpx, "AsyncClient", FakeClient)
    s = FakeSettings()
    s.data["platform.auto_connect"] = True
    result = asyncio.run(platform_probe.probe(FakeDB(), s))
    assert result["dsh"]["healthy"] is True
    assert result["dsh"]["status"] == "connected"
    assert s.data["llm.agent_enabled"] is True
    assert s.data["llm.agent_type"] == "dsh"
    assert s.data["llm.agent_endpoint"].endswith("/novabot/api/chat")
