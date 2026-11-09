"""检测外部 Agent 平台插件是否在线，并在健康时自动接上 DSH。"""

from __future__ import annotations

import ctypes
import logging
import os
import socket
import sys
import time
from ctypes import wintypes

import httpx

log = logging.getLogger(__name__)

DSH_HEALTH = "/novabot/api/health"
DSH_CHAT = "/novabot/api/chat"


def _dsh_process_ports() -> list[int]:
    """用 Win32 TCP 表读本机监听端口，不启动 tasklist/netstat。

    pythonw 下启动控制台子进程会定期闪黑框；这里纯 ctypes 读表，连 conhost 都不创建。
    """
    if sys.platform != "win32":
        return []
    af_inet = 2
    tcp_listener = 3

    class Row(ctypes.Structure):
        _fields_ = [
            ("dwState", wintypes.DWORD),
            ("dwLocalAddr", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("dwRemoteAddr", wintypes.DWORD),
            ("dwRemotePort", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        ]

    class Table(ctypes.Structure):
        _fields_ = [("dwNumEntries", wintypes.DWORD), ("table", Row * 1)]

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    pids: set[int] = set()
    try:
        kernel32 = ctypes.windll.kernel32
        snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)
        if snap and snap != ctypes.c_void_p(-1).value:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            more = kernel32.Process32FirstW(snap, ctypes.byref(entry))
            while more:
                if str(entry.szExeFile).lower() == "deepseek harness.exe":
                    pids.add(int(entry.th32ProcessID))
                more = kernel32.Process32NextW(snap, ctypes.byref(entry))
            kernel32.CloseHandle(snap)
    except Exception:  # noqa: BLE001
        pids.clear()
    if not pids:
        return []

    try:
        iphlpapi = ctypes.windll.iphlpapi
        func = iphlpapi.GetExtendedTcpTable
        func.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.BOOL,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
        ]
        func.restype = wintypes.DWORD
        size = wintypes.DWORD(0)
        func(None, ctypes.byref(size), False, af_inet, tcp_listener, 0)
        if size.value == 0:
            return []
        buf = ctypes.create_string_buffer(size.value)
        ret = func(buf, ctypes.byref(size), False, af_inet, tcp_listener, 0)
        if ret != 0:
            return []
        table = ctypes.cast(buf, ctypes.POINTER(Table)).contents
        base = ctypes.addressof(table) + ctypes.sizeof(wintypes.DWORD)
        step = ctypes.sizeof(Row)
        ports: list[int] = []
        for i in range(int(table.dwNumEntries)):
            row = ctypes.cast(base + i * step, ctypes.POINTER(Row)).contents
            if int(row.dwOwningPid) not in pids:
                continue
            port = socket.ntohs(row.dwLocalPort & 0xFFFF)
            if 1024 <= port <= 65535 and port not in ports:
                ports.append(port)
        return sorted(ports)
    except Exception:  # noqa: BLE001
        return []

def _configured_url(settings) -> str:
    try:
        url = str(settings.get("platform.dsh_url") or "").strip()
    except Exception:  # noqa: BLE001
        url = ""
    if not url:
        url = os.environ.get("DSH_WEB_URL", "http://127.0.0.1:55784")
    return url.rstrip("/")


def _candidate_urls(settings) -> list[str]:
    urls = [_configured_url(settings)]
    env = os.environ.get("DSH_WEB_URL", "").strip().rstrip("/")
    if env:
        urls.append(env)
    for port in _dsh_process_ports():
        urls.append(f"http://127.0.0.1:{port}")
    out = []
    for u in urls:
        if u not in out:
            out.append(u)
    return out


def _recent_sessions(db, platform: str, within: float = 600) -> int:
    try:
        return int(
            db.scalar(
                "SELECT COUNT(*) FROM platform_sessions "
                "WHERE platform = ? AND last_active > ?",
                [platform, time.time() - within],
            )
            or 0
        )
    except Exception:  # noqa: BLE001
        return 0


async def probe(db, settings) -> dict:
    bases = _candidate_urls(settings)
    base = bases[0]
    dsh = {
        "url": base,
        "healthy": False,
        "status": "offline",
        "error": "",
        "chat_endpoint": base + DSH_CHAT,
        "sessions": _recent_sessions(db, "dsh"),
    }
    last_error = ""
    async with httpx.AsyncClient(timeout=2.0) as client:
        for candidate in bases:
            try:
                r = await client.get(candidate + DSH_HEALTH)
                if r.status_code == 200 and (r.json() or {}).get("ok"):
                    base = candidate
                    dsh.update(url=base, healthy=True, status="online", error="",
                               chat_endpoint=base + DSH_CHAT)
                    break
                last_error = f"HTTP {r.status_code}"
                if r.status_code in (401, 403):
                    dsh.update(status="auth", error=last_error, url=candidate,
                               chat_endpoint=candidate + DSH_CHAT)
                else:
                    dsh.update(status="offline", error=last_error, url=candidate,
                               chat_endpoint=candidate + DSH_CHAT)
            except Exception as e:  # noqa: BLE001
                last_error = str(e)[:160]
                dsh.update(status="offline", error=last_error, url=candidate,
                           chat_endpoint=candidate + DSH_CHAT)
    if not dsh["healthy"] and last_error:
        dsh["error"] = last_error
    # 找到新端口就记下来，下次先查它
    if dsh["healthy"]:
        try:
            if str(settings.get("platform.dsh_url") or "").rstrip("/") != base:
                settings.set("platform.dsh_url", base)
        except Exception:  # noqa: BLE001
            pass

    # 健康时按需自动写 Agent 配置（只写不同值，避免每 30 秒刷版本）
    connected = False
    if not dsh["healthy"]:
        # 之前是自动接上的，DSH 关了/插件没开就退回普通模型，别让回复全失败
        try:
            if (bool(settings.get("platform.auto_connect"))
                    and bool(settings.get("llm.agent_enabled"))
                    and str(settings.get("llm.agent_endpoint") or "") == dsh["chat_endpoint"]):
                settings.set("llm.agent_enabled", False)
        except Exception:  # noqa: BLE001
            pass
    if dsh["healthy"]:
        try:
            if bool(settings.get("platform.auto_connect")):
                wanted = {
                    "llm.agent_enabled": True,
                    "llm.agent_type": "dsh",
                    "llm.agent_endpoint": dsh["chat_endpoint"],
                }
                for key, value in wanted.items():
                    if settings.get(key) != value:
                        settings.set(key, value)
                connected = True
                dsh["status"] = "connected"
        except Exception as e:  # noqa: BLE001
            dsh["error"] = f"自动连接失败：{e}"

    return {
        "ok": True,
        "at": time.time(),
        "dsh": dsh,
        "trae": {
            "status": "mcp",
            "sessions": _recent_sessions(db, "trae"),
            "note": "MCP 客户端；插件调用后会话会出现在映射里",
        },
        "codebuddy": {
            "status": "mcp",
            "sessions": _recent_sessions(db, "codebuddy"),
            "note": "MCP 客户端；插件调用后会话会出现在映射里",
        },
        "connected": connected,
    }
