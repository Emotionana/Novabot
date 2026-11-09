"""测试/冒烟脚本的进程和临时目录清理小工具。

原则：谁起的进程谁负责收尸，收不干净就交给 cleanup_test_procs.py 扫。
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import time
from pathlib import Path


def kill_tree(pid: int) -> None:
    """杀掉整个进程树。Windows 用 taskkill /T，其他平台退回 terminate。"""
    if not pid:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=10,
            )
            return
        except Exception:  # noqa: BLE001
            pass
    with contextlib.suppress(Exception):
        os.kill(pid, 9)


def wait_or_kill(proc, timeout: float = 8.0) -> None:
    """先等它自己退，超时就杀整棵树。"""
    try:
        proc.wait(timeout=timeout)
        return
    except Exception:  # noqa: BLE001
        pass
    kill_tree(getattr(proc, "pid", 0))


def cleanup_dir(path, *, retries: int = 3) -> None:
    """删临时目录。浏览器刚退出时句柄可能还没释放，重试几次。"""
    if not path:
        return
    for i in range(max(1, retries)):
        shutil.rmtree(str(path), ignore_errors=True)
        if not Path(str(path)).exists():
            return
        time.sleep(0.4 * (i + 1))
