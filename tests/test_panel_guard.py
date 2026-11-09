"""面板本机 / Host / CSRF 闸。"""

from __future__ import annotations

from app.web.panel_guard import check_panel_request


def headers(**kw):
    return {k.replace("_", "-"): v for k, v in kw.items()}


def test_local_same_origin_post_allowed():
    assert check_panel_request(
        "POST", "/api/settings/set",
        headers(host="127.0.0.1:19100", origin="http://127.0.0.1:19100", sec_fetch_site="same-origin"),
        "127.0.0.1",
    ) is None


def test_remote_client_blocked_when_remote_off():
    assert check_panel_request("GET", "/api/info", headers(host="127.0.0.1:19100"), "8.8.8.8")


def test_foreign_host_blocked():
    assert check_panel_request("GET", "/api/info", headers(host="evil.example:19100"), "127.0.0.1")


def test_cross_site_post_blocked():
    reason = check_panel_request(
        "POST", "/api/settings/set",
        headers(host="127.0.0.1:19100", origin="https://evil.example", sec_fetch_site="cross-site"),
        "127.0.0.1",
    )
    assert reason and "CSRF" in reason


def test_remote_mode_allows_remote_host():
    assert check_panel_request(
        "GET", "/api/info", headers(host="panel.example.com"), "203.0.113.5", panel_remote=True,
    ) is None
