"""面板 / 平台接口的令牌校验。

规则：
- 面板密码为空 = 不校验（本机默认）
- 面板密码非空 = /api/* 必须带 X-Panel-Token（或 ?token=）
- /api/platform/* 优先用 platform.token；没配就退回面板密码
"""

from __future__ import annotations


def check_request(
    path: str,
    headers,
    query,
    *,
    panel_password: str = "",
    platform_token: str = "",
) -> str | None:
    """返回 None 表示放行，否则返回拒绝原因。"""
    if not path.startswith("/api/"):
        return None

    if path.startswith("/api/platform/") and platform_token:
        sent = _header(headers, "x-platform-token")
        return None if sent == platform_token else "platform token 无效"

    if panel_password:
        sent = _header(headers, "x-panel-token") or (query.get("token") or "")
        return None if sent == panel_password else "需要面板密码"

    return None


def _header(headers, name: str) -> str:
    """大小写不敏感；普通 dict 和 Starlette Headers 都能用。"""
    try:
        for k, v in headers.items():
            if str(k).lower() == name:
                return str(v or "")
    except Exception:  # noqa: BLE001
        pass
    return ""
