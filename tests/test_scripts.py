"""把原来的 12 个手工脚本纳入 pytest。

它们本来就能跑（`python -m tests.test_xxx`），这里只做包装，
不再让 `pytest` 收集到 0 个测试。
"""

from __future__ import annotations

import asyncio
import importlib

import pytest

SCRIPTS = [
    "tests.test_smoke",
    "tests.test_attention",
    "tests.test_gateway",
    "tests.test_cortex",
    "tests.test_amygdala",
    "tests.test_graph",
    "tests.test_persona",
    "tests.test_purge",
    "tests.test_recall",
    "tests.test_settings",
    "tests.test_voice_select",
]


@pytest.mark.parametrize("module", SCRIPTS)
def test_script(module: str) -> None:
    m = importlib.import_module(module)
    assert m.main() == 0


def test_pipeline_script() -> None:
    from tests.test_pipeline import run

    assert asyncio.run(run()) == 0


def test_migration_backs_up_before_upgrade(db):
    """版本落后时先备份，再迁移。"""
    from app.memory.migrate import init_schema

    db.execute("UPDATE meta SET v = '7' WHERE k = 'schema_version'")
    init_schema(db)

    backups = list((db.path.parent / "backups").glob("pre-v7-*.db"))
    assert backups, "迁移前没有备份"
    assert backups[0].stat().st_size > 0
