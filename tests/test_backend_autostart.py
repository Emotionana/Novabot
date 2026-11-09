"""OneBot 连接插件自动拉起。"""

from __future__ import annotations

from app.gateway.backend import ensure_backend, start_backend
from app.settings import Settings


def test_start_skips_when_port_open(db, monkeypatch):
    monkeypatch.setattr("app.gateway.backend.port_open", lambda host, port: True)
    result = start_backend(db, command="whatever.exe", port=3001)
    assert result["started"] is False
    assert "已在监听" in result["reason"]


def test_ensure_reports_missing_command(db):
    st = Settings(db)
    st.set("gateway.backend_autostart", True)
    st.set("gateway.backend_command", "")
    result = ensure_backend(db)
    assert result["started"] is False
    assert "没有配置" in result["reason"]
