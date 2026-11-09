"""清理测试/冒烟留下的东西。

只动测试特征，不碰正在用的服务：
    - 测试起的 uvicorn：命令行里同时有 app.main:app 和 --log-level warning
    - headless 浏览器：命令行里带 pb-ui- 临时 profile
    - 临时目录：novabot-* / pb-ui-* / pb-voice-auto-*

用法：
    python -m scripts.cleanup_test_procs              # 清进程 + 30 分钟前的目录
    python -m scripts.cleanup_test_procs --all        # 目录也全清（确认没在跑测试）
    python -m scripts.cleanup_test_procs --dry-run    # 只看，不动手
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TEMP_PREFIXES = ("novabot-", "pb-ui-", "pb-voice-auto-")

PS_QUERY = r"""
$out = Get-CimInstance Win32_Process | Where-Object {
  ($_.Name -eq 'msedge.exe' -and $_.CommandLine -like '*pb-ui-*') -or
  ($_.Name -match '^python(w)?\.exe$' -and
   $_.CommandLine -like '*app.main:app*' -and
   $_.CommandLine -like '*--log-level warning*')
} | Select-Object ProcessId, Name, CommandLine
$out | ConvertTo-Json -Compress
"""


def _find_test_processes() -> list[dict]:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", PS_QUERY],
            capture_output=True,
            text=True,
            timeout=30,
        )
        raw = (r.stdout or "").strip()
        if not raw:
            return []
        data = json.loads(raw)
        return data if isinstance(data, list) else [data]
    except Exception as e:  # noqa: BLE001
        print(f"（查进程失败：{e}）")
        return []


def _kill(pid: int) -> None:
    with contextlib.suppress(Exception):
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True,
            timeout=10,
        )


def _old_dirs(min_age_sec: float) -> list[Path]:
    now = time.time()
    out: list[Path] = []
    base = Path(tempfile.gettempdir())
    for prefix in TEMP_PREFIXES:
        for p in base.glob(prefix + "*"):
            if not p.is_dir():
                continue
            try:
                if min_age_sec <= 0 or now - p.stat().st_mtime >= min_age_sec:
                    out.append(p)
            except OSError:
                continue
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="清理测试残留")
    parser.add_argument("--all", action="store_true", help="临时目录全清，不看年龄")
    parser.add_argument("--min-age-min", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    procs = _find_test_processes()
    dirs = _old_dirs(0 if args.all else args.min_age_min * 60)

    print(f"测试进程：{len(procs)}")
    for p in procs:
        cmd = (p.get("CommandLine") or "")[:90]
        print(f"  pid {p.get('ProcessId')}  {cmd}")
    print(f"临时目录：{len(dirs)}")

    if args.dry_run:
        print("\n--dry-run：没动。")
        return 0

    for p in procs:
        _kill(int(p["ProcessId"]))
    removed = 0
    for d in dirs:
        try:
            shutil.rmtree(d)
            removed += 1
        except OSError:
            pass

    print(f"\n已结束 {len(procs)} 个测试进程，删除 {removed} 个临时目录。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
