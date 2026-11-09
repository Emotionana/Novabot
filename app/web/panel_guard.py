"""面板管理接口的本机 / Host / CSRF 闸。"""

from __future__ import annotations

from urllib.parse import urlsplit

LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient", "testserver"}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def _host_only(value: str) -> str:
    value = str(value or "").strip().lower()
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def _origin_host(value: str) -> tuple[str, str]:
    try:
        u = urlsplit(str(value or ""))
        host = (u.hostname or "").lower()
        port = str(u.port or (443 if u.scheme == "https" else 80))
        return host, port
    except Exception:  # noqa: BLE001
        return "", ""


def check_panel_request(method: str, path: str, headers, client_host: str = "", *, panel_remote: bool = False) -> str | None:
    """返回 None 表示放行，否则返回 403 原因。只闸 /api/*。"""
    if not str(path or "").startswith("/api/"):
        return None

    host_header = str(headers.get("host") or "").strip()
    host = _host_only(host_header)
    remote = bool(panel_remote)
    if not host:
        return "缺少 Host"
    if not remote and host not in LOOPBACK:
        return "只允许本机 Host 访问管理接口"

    client = str(client_host or "").split("%", 1)[0].strip("[]")
    if not remote and client and client not in LOOPBACK:
        return "只允许本机访问管理接口"

    if str(method or "").upper() not in UNSAFE:
        return None

    site = str(headers.get("sec-fetch-site") or "").strip().lower()
    if site in {"cross-site", "same-site"}:
        return f"CSRF 拒绝（Sec-Fetch-Site={site}）"

    origin = str(headers.get("origin") or "").strip()
    if origin:
        oh, op = _origin_host(origin)
        rh, rp = _origin_host("http://" + host_header.replace("https://", "").replace("http://", ""))
        if not oh or oh != rh or op != rp:
            return "CSRF 拒绝（Origin 与 Host 不一致）"
    return None
