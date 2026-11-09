"""OneBot 连接插件的自动拉起。

SnowLuma/NapCat 这类插件不是 Novabot 子进程；默认只重连。
这里的逻辑：如果配置了自动拉起，且插件端口没监听，就执行命令启动一次。
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import time
from pathlib import Path

from ..settings import Settings

log = logging.getLogger(__name__)


def port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    if not port:
        return False
    try:
        with socket.create_connection((host or "127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _check_target(db, settings: Settings) -> tuple[str, int]:
    try:
        port = int(settings.get("gateway.backend_check_port") or 0)
    except Exception:  # noqa: BLE001
        port = 0
    host = "127.0.0.1"
    if port:
        return host, port
    # 没填端口就从第一个启用账号的 OneBot URL 推
    try:
        row = db.query_one(
            "SELECT url FROM accounts WHERE enabled = 1 AND url LIKE 'ws://%' ORDER BY sort_order, id LIMIT 1"
        )
        if row and row["url"]:
            rest = str(row["url"]).split("://", 1)[1].split("/", 1)[0]
            h, p = rest.rsplit(":", 1)
            return h or host, int(p)
    except Exception:  # noqa: BLE001
        pass
    return host, 3001


def start_backend(db, *, command: str = "", workdir: str = "", port: int = 0) -> dict:
    """启动一次；返回是否真的启动，不等待端口就绪。"""
    command = str(command or "").strip()
    if not command:
        return {"started": False, "reason": "没有配置连接插件命令"}
    cwd = str(workdir or "").strip() or None
    if cwd and not Path(cwd).is_dir():
        return {"started": False, "reason": f"工作目录不存在：{cwd}"}
    if port and port_open("127.0.0.1", port):
        return {"started": False, "reason": f"端口 {port} 已在监听，不重复启动"}

    log_path = Path(getattr(db, "path", ".")).parent / "logs" / "backend-autostart.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        with log_path.open("ab") as out:
            out.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] start: {command} (cwd={cwd})\n".encode())
            out.flush()
            proc = subprocess.Popen(
                command,
                cwd=cwd,
                shell=True,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                creationflags=flags,
                close_fds=True,
            )
        log.info("已拉起 OneBot 连接插件 PID=%s：%s", proc.pid, command)
        return {"started": True, "pid": proc.pid, "port": port, "command": command}
    except Exception as e:  # noqa: BLE001
        log.warning("拉起连接插件失败：%s", e)
        return {"started": False, "reason": str(e)}


def ensure_backend(db) -> dict:
    settings = Settings(db)
    try:
        enabled = bool(settings.get("gateway.backend_autostart"))
        command = str(settings.get("gateway.backend_command") or "")
        workdir = str(settings.get("gateway.backend_workdir") or "")
    except Exception as e:  # noqa: BLE001
        return {"started": False, "reason": f"读取配置失败：{e}"}
    if not enabled:
        return {"started": False, "reason": "自动拉起未开启"}
    _host, port = _check_target(db, settings)
    return start_backend(db, command=command, workdir=workdir, port=port)
