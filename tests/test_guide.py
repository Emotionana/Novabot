"""使用指南必须存在，并且跟着 schema/主要功能同步。"""

from __future__ import annotations

from pathlib import Path

from app.memory.migrate import SCHEMA_VERSION

GUIDE = Path(__file__).resolve().parent.parent / "使用指南.txt"


def test_guide_exists_and_covers_current_schema():
    assert GUIDE.is_file(), "使用指南.txt 不见了"
    text = GUIDE.read_text(encoding="utf-8-sig")

    assert len(text) > 5000, "指南太短，像是没写"
    assert f"schema v{SCHEMA_VERSION}" in text, "schema 升级了，指南没同步"
    for key in ("启动Novabot.bat", "工作台", "多开", "拖动排序", "设置页", "编辑", "搜文档", "改时间", "预览", "人格卡", "模块标签", "遗忘", "关键词", "手写区", "check_modules", "smoke_auth", "面板密码"):
        assert key in text, f"指南缺少关键内容：{key}"
