"""Novabot 桌面端：WebView2 窗口 + 同进程组后端。

窗口关闭时先确认；确认后杀掉后端进程组，不再留 pythonw/uvicorn。
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ICON = ROOT / "assets" / "novabot.ico"
BACKEND_PY = ROOT / ".venv" / "Scripts" / "pythonw.exe"


import webview  # noqa: E402

from scripts._procutil import kill_tree  # noqa: E402


def _message(text: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(0, text, "Novabot", 0x10)
    except Exception:  # noqa: BLE001
        print(text)


def _port() -> int:
    try:
        from app import config

        return int(config.get().server.port or 19100)
    except Exception:  # noqa: BLE001
        return 19100


def _health(port: int, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.25)
    return False


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", int(port)))
            return True
        except OSError:
            return False


def _next_free_port(start: int) -> int:
    for port in range(int(start), int(start) + 100):
        if _port_free(port):
            return port
    return 0


def _already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


class Desktop:
    def __init__(self) -> None:
        self.port = _port()
        self.proc: subprocess.Popen | None = None
        self.window: webview.Window | None = None
        self.smoke = "--smoke" in sys.argv
        self._stopping = False

    def start_server(self) -> bool:
        if _already_running(self.port):
            _message(f"Novabot 已经在 {self.port} 端口运行。\n请先关闭旧实例，再启动桌面端。")
            return False
        logs = ROOT / "data" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        out = (logs / "desktop-server.out.log").open("ab")
        err = (logs / "desktop-server.err.log").open("ab")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            [str(BACKEND_PY if BACKEND_PY.is_file() else sys.executable),
             "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            creationflags=flags,
            close_fds=True,
        )
        if not _health(self.port):
            self.stop_server()
            _message("Novabot 后端没有在 40 秒内启动。\n查看 data/logs/desktop-server.err.log。")
            return False
        return True

    def stop_server(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        if self.proc is not None and self.proc.poll() is None:
            # 先请求正常退出，让 uvicorn 跑完 lifespan；超时才强杀进程树。
            with contextlib.suppress(Exception):
                self.proc.terminate()
            with contextlib.suppress(Exception):
                self.proc.wait(timeout=3)
            if self.proc.poll() is None:
                with contextlib.suppress(Exception):
                    kill_tree(self.proc.pid)
                with contextlib.suppress(Exception):
                    self.proc.wait(timeout=5)

    def on_closing(self) -> bool:
        if self._stopping:
            return True
        if self.smoke:
            self.stop_server()
            return True
        if self.window is None:
            return True
        ok = self.window.create_confirmation_dialog(
            "关闭 Novabot",
            "你确定关闭 Novabot 吗？\n\n关闭后回复立即停止，后端进程会全部退出，不会残留后台。",
        )
        if not ok:
            return False
        self.stop_server()
        return True

    def watch_server(self) -> None:
        if self.proc is None:
            return
        self.proc.wait()
        # 用户已经点关闭并确认时，窗口正在由 pywebview 主循环收尾；
        # 这里再从后台线程 destroy 一次会撞上 WebView2 的关闭流程。
        if self._stopping:
            return
        self._stopping = True
        if self.window is not None:
            with contextlib.suppress(Exception):
                self.window.destroy()

    def run(self) -> int:
        existing = _already_running(self.port)
        if not existing and not _port_free(self.port):
            port = _next_free_port(self.port + 1)
            if not port:
                _message(f"端口 {self.port} 被占用，附近也没有空闲端口。")
                return 1
            self.port = port
        if not existing and not self.start_server():
            return 1
        self.window = webview.create_window(
            "Novabot",
            f"http://127.0.0.1:{self.port}/?desktop=1",
            width=1280,
            height=860,
            min_size=(900, 600),
            background_color="#F6F7F9",
        )
        self.window.events.closing += self.on_closing
        threading.Thread(target=self.watch_server, daemon=True).start()
        if self.smoke:
            threading.Thread(target=self._smoke_close, daemon=True).start()
        try:
            webview.start(
                gui="edgechromium",
                private_mode=False,
                storage_path=str(ROOT / "data" / "webview"),
                icon=str(ICON) if ICON.is_file() else None,
            )
        except Exception:
            log_path = ROOT / "data" / "logs" / "desktop-ui.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
                import traceback
                traceback.print_exc(file=f)
            return 1
        finally:
            self.stop_server()
        return 0

    def _smoke_close(self) -> None:
        time.sleep(3)
        try:
            if self.window is not None:
                self.window.destroy()
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    if os.name != "nt":
        _message("Novabot 桌面端目前只支持 Windows。")
        return 1
    return Desktop().run()


if __name__ == "__main__":
    raise SystemExit(main())
