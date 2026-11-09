"""模型自动检测。"""

from __future__ import annotations

import time

import pytest

from app.model_detect import agent_chat_url, agent_health_url, detect_and_apply
from app.settings import Settings


def test_url_normalization():
    assert agent_chat_url("http://127.0.0.1:52581") == "http://127.0.0.1:52581/novabot/api/chat"
    assert agent_health_url("http://127.0.0.1:52581/novabot/api/chat") == "http://127.0.0.1:52581/novabot/api/health"
    assert agent_health_url("http://127.0.0.1:52581") == "http://127.0.0.1:52581/novabot/api/health"


@pytest.mark.asyncio
async def test_detect_applies_agent_to_accounts(db, monkeypatch):
    now = time.time()
    db.execute(
        "INSERT OR IGNORE INTO accounts (id, name, enabled, created_at, updated_at) VALUES (?,?,?,?,?)",
        ["acc-a", "A", 1, now, now],
    )
    st = Settings(db, account_id="acc-a")
    st.set("llm.agent_auto", True)
    st.set("llm.agent_enabled", False)
    st.set("llm.agent_endpoint", "")

    monkeypatch.setattr("app.model_detect.candidates", lambda _db: ["http://127.0.0.1:52581/novabot/api/chat"])

    async def fake_probe(endpoint, client=None):
        return True

    monkeypatch.setattr("app.model_detect.probe", fake_probe)
    result = await detect_and_apply(db)
    assert result["ok"] is True
    assert "acc-a" in result["accounts"]
    st2 = Settings(db, account_id="acc-a")
    assert st2.get("llm.agent_enabled") is True
    assert st2.get("llm.agent_type") == "dsh"
    assert st2.get("llm.agent_endpoint").endswith("/novabot/api/chat")
