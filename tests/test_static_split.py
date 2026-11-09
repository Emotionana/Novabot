"""前端静态文件拆分后，入口必须加载三块，不能退回单个 app.js。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "app" / "web" / "static"


def test_index_loads_split_scripts():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "app.core.js" in html
    assert "app.pages.js" in html
    assert "app.main.js" in html
    assert "/static/app.js" not in html
    for name in ("app.core.js", "app.pages.js", "app.main.js", "app.css"):
        assert (ROOT / name).is_file(), name
