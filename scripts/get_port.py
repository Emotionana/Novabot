"""打印配置里的端口，给 .bat 用。

读不到就输出 19100，保证批处理永远有值可用。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    port = 19100
    try:
        from app import config

        port = int(config.get().server.port)
    except Exception:
        pass
    print(port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
