"""鉴权冒烟：面板密码 + 平台令牌。

起一个临时实例（临时库），验证：
- 无 token 401
- 面板密码可以访问 /api/info
- 平台接口用独立的 platform.token
"""

from __future__ import annotations

import atexit
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.params  # noqa: E402,F401
from app.db import Database  # noqa: E402
from app.memory.migrate import init_schema  # noqa: E402
from app.settings import Settings  # noqa: E402
from scripts._procutil import cleanup_dir, kill_tree  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="novabot-auth-"))
    atexit.register(cleanup_dir, tmp)
    db_path = tmp / "auth.db"
    db = Database(db_path)
    init_schema(db)
    s = Settings(db)
    s.set("gateway.enabled", False)
    s.set("security.panel_password", "test-pw")
    s.set("platform.token", "test-platform")
    db.close()

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["NOVABOT_DATABASE__PATH"] = str(db_path)
    env["NOVABOT_LLM__DEFAULT"] = "mock"
    env["NOVABOT_LLM__MOCK__TYPE"] = "mock"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )

    problems = []
    log = ""
    try:
        with httpx.Client(timeout=5) as c:
            for _ in range(60):
                try:
                    if c.get(base + "/healthz").status_code == 200:
                        break
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(0.3)
            else:
                raise RuntimeError("服务没起来")

            no_token = c.get(base + "/api/info")
            print("无 token：", no_token.status_code)
            if no_token.status_code != 401:
                problems.append("无 token 竟然能访问")

            wrong = c.get(base + "/api/info", headers={"X-Panel-Token": "bad"})
            print("错 token：", wrong.status_code)
            if wrong.status_code != 401:
                problems.append("错 token 没拦住")

            ok = c.get(base + "/api/info", headers={"X-Panel-Token": "test-pw"})
            print("面板密码：", ok.status_code)
            if ok.status_code != 200:
                problems.append("正确面板密码被拒")

            p_no = c.get(base + "/api/platform/sessions")
            p_ok = c.get(base + "/api/platform/sessions", headers={"X-Platform-Token": "test-platform"})
            print("平台无 token：", p_no.status_code, "正确：", p_ok.status_code)
            if p_no.status_code != 401 or p_ok.status_code != 200:
                problems.append("platform token 校验不对")

        # 真浏览器：登录框 → 输密码 → 六个页面
        print("浏览器登录验证…")
        ui = subprocess.run(
            [sys.executable, "-m", "scripts.smoke_ui",
             "--url", base, "--password", "test-pw"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=240,
        )
        print((ui.stdout or "").strip().splitlines()[-1] if ui.stdout else "")
        if ui.returncode != 0:
            problems.append("浏览器登录/页面冒烟失败")
            print((ui.stdout or "")[-1200:])
            print((ui.stderr or "")[-600:])
    except Exception as e:  # noqa: BLE001
        problems.append(f"{type(e).__name__}: {e}")
    finally:
        kill_tree(proc.pid)
        try:
            log, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            log, _ = proc.communicate()
        cleanup_dir(tmp)

    if problems:
        print("\n！鉴权冒烟失败：")
        for p in problems:
            print("  -", p)
        print("\n--- 日志尾部 ---")
        for line in (log or "").splitlines()[-20:]:
            print(" ", line)
        return 1

    print("\n鉴权冒烟通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
