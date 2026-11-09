"""等 /healthz 返回 ok。

用法：
    python scripts/wait_healthy.py [端口] [最长等待秒数]

退出码 0 = 健康；1 = 超时。
给 .bat 用：起来之前先探一下，已经在跑就不用再起一个。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request


def healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1.5) as resp:
            return bool(json.loads(resp.read().decode("utf-8")).get("ok"))
    except Exception:
        return False


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 19100
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 40.0

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if healthy(port):
            return 0
        time.sleep(0.5)
    return 1


if __name__ == "__main__":
    sys.exit(main())
