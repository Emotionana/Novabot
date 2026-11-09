"""模块自检：跑一遍，看每个模块是不是真的接上了。

用法：
    python -m scripts.check_modules

状态：ok / warn / off / missing
- ok      能用，有数据或开关开着
- warn    代码在，但缺配置/没实现完
- off     开关关着（正常）
- missing 表或模块缺失（要处理）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.params  # noqa: E402,F401
from app.db import get_db  # noqa: E402
from app.system_status import collect, summary  # noqa: E402

ICON = {"ok": "✓", "warn": "!", "off": "·", "missing": "✗"}


def main() -> int:
    db = get_db()
    rows = collect(db)
    s = summary(rows)

    print(f"{'状态':<4} {'模块':<18} 说明")
    print("-" * 66)
    for r in rows:
        print(f"{ICON.get(r['status'], '?'):<4} {r['name']:<18} {r['detail']}")

    print(
        f"\n合计 {s['total']} 个：ok {s['ok']} / warn {s['warn']} / "
        f"off {s['off']} / missing {s['missing']}"
    )
    db.close()
    return 1 if s["missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
