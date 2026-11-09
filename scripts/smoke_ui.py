"""界面冒烟：用无头浏览器把网页真的渲染一遍。

为什么需要它：
    单元测试和接口测试全绿，页面照样可能白屏——内联 JS 里少一个括号就够了。
    （这个脚本就是因为"打开看看是白屏"才写的。）

它做的事：
    1. 起一个无头 Edge/Chrome（CDP 控制）
    2. 打开页面，收集 JS 异常和 console 报错
    3. 依次切到 概览/人物/记忆/接入/设置，检查每页标题渲染出来
    4. 没浏览器就跳过（退出码 0，打印"跳过"）

用法：
    python -m scripts.smoke_ui                  # 默认 http://127.0.0.1:19100
    python -m scripts.smoke_ui --url http://127.0.0.1:19101
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._procutil import cleanup_dir, kill_tree  # noqa: E402

BROWSERS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)

PAGES = ["overview", "workbench", "people", "persona", "memory", "voice", "platform", "modules", "proactive", "gateway", "settings", "backup"]
TITLES = {
    "overview": "概览",
    "workbench": "工作台",
    "people": "人物",
    "persona": "人格",
    "memory": "记忆",
    "voice": "语音",
    "platform": "平台",
    "modules": "模块",
  "proactive": "生活",
  "backup": "备份与恢复",
    "gateway": "接入",
    "settings": "设置",
}


def find_browser() -> str | None:
    for path in BROWSERS:
        if Path(path).is_file():
            return path
    return None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def cdp_session(
    debug_port: int,
    url: str,
    *,
    shot_path: str = "",
    shot_page: str = "",
    theme: str = "",
    width: int = 1280,
    height: int = 900,
    password: str = "",
    shot_js: str = "",
) -> tuple[list[str], dict]:
    """连上浏览器，打开页面，切几页，返回 (错误列表, 状态)。"""
    import websockets

    targets = None
    for _ in range(80):
        try:
            targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{debug_port}/json"))
            break
        except Exception:
            await asyncio.sleep(0.25)
    if not targets:
        raise RuntimeError("连不上浏览器的调试端口")

    page = next(t for t in targets if t["type"] == "page")
    errors: list[str] = []
    state: dict = {}

    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=2**24) as ws:
        seq = 0

        async def send(method: str, params: dict | None = None) -> int:
            nonlocal seq
            seq += 1
            await ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
            return seq

        async def drain(seconds: float) -> None:
            end = time.time() + seconds
            while time.time() < end:
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.3))
                except TimeoutError:
                    continue
                method = msg.get("method")
                if method == "Runtime.exceptionThrown":
                    d = msg["params"]["exceptionDetails"]
                    desc = (d.get("exception") or {}).get("description") or d.get("text") or "?"
                    errors.append(f"JS 异常 @行{d.get('lineNumber')}: {desc[:300]}")
                elif method == "Log.entryAdded":
                    entry = msg["params"]["entry"]
                    if entry.get("level") == "error" and "favicon" not in (entry.get("url") or ""):
                        errors.append(f"console 错误: {str(entry.get('text'))[:300]}")
                elif method == "Runtime.consoleAPICalled" and msg["params"].get("type") == "error":
                    args = " ".join(str(a.get("value") or a.get("description")) for a in msg["params"]["args"])
                    errors.append(f"console.error: {args[:300]}")

        async def call(method: str, params: dict | None = None):
            nonlocal seq
            seq += 1
            my_id = seq
            await ws.send(
                json.dumps({"id": my_id, "method": method, "params": params or {}})
            )
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                if msg.get("id") == my_id:
                    return msg.get("result") or {}

        async def evaluate(expression: str, await_promise: bool = False):
            nonlocal seq
            seq += 1
            my_id = seq
            await ws.send(
                json.dumps(
                    {
                        "id": my_id,
                        "method": "Runtime.evaluate",
                        "params": {
                            "expression": expression,
                            "returnByValue": True,
                            "awaitPromise": await_promise,
                        },
                    }
                )
            )
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if msg.get("id") == my_id:
                    return (msg.get("result") or {}).get("result", {}).get("value")

        for method in ("Runtime.enable", "Log.enable", "Page.enable"):
            await send(method)

        await send("Page.navigate", {"url": url})
        await drain(4.0)

        if password:
            shown = False
            for _ in range(20):
                shown = bool(
                    await evaluate(
                        "document.querySelector('#login-mask')?.classList.contains('show')"
                    )
                )
                if shown:
                    break
                await asyncio.sleep(0.3)
            if not shown:
                errors.append("登录框没出现")
            else:
                await evaluate(
                    "(async () => {"
                    " const i = document.querySelector('#login-token');"
                    f" i.value = {json.dumps(password)};"
                    " await loginSubmit();"
                    " await new Promise(r => setTimeout(r, 1200));"
                    "})()",
                    await_promise=True,
                )
                await drain(0.6)
                hidden = bool(
                    await evaluate(
                        "!document.querySelector('#login-mask')?.classList.contains('show')"
                    )
                )
                if hidden:
                    # 登录前的 401 是预期内的，清掉，别算成页面错误
                    errors.clear()
                else:
                    errors.append("登录失败：登录框没有关闭")

        state = await evaluate(
            "JSON.stringify({"
            "nav: document.querySelectorAll('#nav .nav-item').length,"
            "title: document.querySelector('.page-title')?.textContent || '',"
            "status: document.querySelector('#status-text')?.textContent || '',"
            "stats: [...document.querySelectorAll('.stat-value')].map(e=>e.textContent.trim()).slice(0,6)"
            "})"
        )

        # 逐页切换，检查标题渲染出来
        page_results = {}
        for pid in PAGES:
            value = await evaluate(
                "(async () => {"
                f"  go('{pid}');"
                "  await new Promise(r => setTimeout(r, 1300));"
                "  return JSON.stringify({title: document.querySelector('.page-title')?.textContent || '',"
                "    body: (document.querySelector('#main').innerText || '').slice(0, 60)});"
                "})()",
                await_promise=True,
            )
            await drain(0.4)
            page_results[pid] = json.loads(value) if value else {}

        state = json.loads(state) if state else {}
        state["pages"] = page_results

        if theme:
            await evaluate(f"setTheme('{theme}')")
            await drain(0.3)

        if shot_path:
            if shot_page:
                await evaluate(
                    f"(async () => {{ go('{shot_page}'); await new Promise(r => setTimeout(r, 800)); }})()",
                    await_promise=True,
                )
                await drain(0.5)
            if shot_js:
                await evaluate(
                    f"(async () => {{ {shot_js}; await new Promise(r => setTimeout(r, 500)); }})()",
                    await_promise=True,
                )
                await drain(0.5)
            result = await call("Page.captureScreenshot", {"format": "png"})
            data = result.get("data")
            if data:
                import base64

                Path(shot_path).parent.mkdir(parents=True, exist_ok=True)
                Path(shot_path).write_bytes(base64.b64decode(data))
                state["shot"] = shot_path

    return errors, state


async def main() -> int:
    parser = argparse.ArgumentParser(description="Novabot 界面冒烟")
    parser.add_argument("--url", default="http://127.0.0.1:19100")
    parser.add_argument("--shot", default="", help="截图存到哪（可选）")
    parser.add_argument("--shot-page", default="", help="截图前切到哪一页")
    parser.add_argument("--theme", default="", help="截图前切到哪个主题")
    parser.add_argument("--password", default="", help="面板密码（测登录框）")
    parser.add_argument("--shot-js", default="", help="截图前执行的 JS（如 openAccountsModal()）")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=900)
    args = parser.parse_args()

    browser = find_browser()
    if browser is None:
        print("没找到 Edge/Chrome，跳过界面冒烟")
        return 0

    debug_port = free_port()
    profile = tempfile.mkdtemp(prefix="pb-ui-")
    atexit.register(cleanup_dir, profile)
    proc = subprocess.Popen(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            f"--window-size={args.width},{args.height}",
            f"--remote-debugging-port={debug_port}",
            f"--user-data-dir={profile}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        url = args.url.rstrip("/") + "/?t=" + str(int(time.time()))
        errors, state = await cdp_session(
            debug_port,
            url,
            shot_path=args.shot,
            shot_page=args.shot_page,
            theme=args.theme,
            width=args.width,
            height=args.height,
            password=args.password,
            shot_js=args.shot_js,
        )
    finally:
        kill_tree(proc.pid)
        cleanup_dir(profile)

    print(f"浏览器：{browser}")
    if state.get("shot"):
        print(f"截图：{state['shot']}")
    print(f"侧边栏项目：{state.get('nav', 0)}")
    print(f"当前页：{state.get('title', '')!r}   状态栏：{state.get('status', '')!r}")
    print(f"概览数字：{state.get('stats')}")

    problems: list[str] = []
    for pid, info in (state.get("pages") or {}).items():
        title = (info or {}).get("title", "")
        body = (info or {}).get("body", "").replace("\n", " ")
        ok = TITLES[pid] in title
        print(f"  {'ok ' if ok else 'FAIL'} {pid:9s} {title!r}  {body[:40]}")
        if not ok:
            problems.append(f"{pid} 页没渲染出来（标题 {title!r}）")

    for err in errors:
        print(f"  !! {err}")

    if state.get("nav", 0) < len(PAGES):
        problems.append(f"侧边栏只有 {state.get('nav')} 项，应为 {len(PAGES)}")
    if errors:
        problems.append(f"页面有 {len(errors)} 条 JS 错误")

    if problems:
        print("\n！界面冒烟失败：")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("\n界面冒烟通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
