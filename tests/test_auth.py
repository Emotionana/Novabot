"""面板/平台令牌校验。"""

from __future__ import annotations

from app.web.auth import check_request


def check(path, headers=None, query=None, **kw):
    return check_request(path, headers or {}, query or {}, **kw)


def test_no_password_allows_all():
    assert check("/api/info") is None


def test_panel_password_requires_token():
    assert check("/api/info", panel_password="pw") == "需要面板密码"
    assert check("/api/info", {"X-Panel-Token": "pw"}, panel_password="pw") is None
    assert check("/api/info", query={"token": "pw"}, panel_password="pw") is None
    assert check("/api/info", {"X-Panel-Token": "bad"}, panel_password="pw")


def test_platform_token_is_separate():
    kw = {"panel_password": "pw", "platform_token": "pt"}
    assert check("/api/platform/sessions", **kw) == "platform token 无效"
    assert check("/api/platform/sessions", {"X-Platform-Token": "pt"}, **kw) is None
    # 平台接口带面板密码不算数
    assert check("/api/platform/sessions", {"X-Panel-Token": "pw"}, **kw)


def test_platform_falls_back_to_panel_password():
    assert check("/api/platform/sessions", panel_password="pw") == "需要面板密码"
    assert check("/api/platform/sessions", {"X-Panel-Token": "pw"}, panel_password="pw") is None
