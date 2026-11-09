"""pytest 公共配置与夹具。

把仓库根目录放进 sys.path，这样不装包也能 `pytest`；
顺手加载参数注册表，避免每个测试各自 import。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.params  # noqa: E402,F401  注册所有参数


@pytest.fixture()
def db(tmp_path):
    """一个建好表的一次性数据库。"""
    from app.db import Database
    from app.memory.migrate import init_schema

    database = Database(tmp_path / "test.db")
    init_schema(database)
    yield database
    database.close()


@pytest.fixture()
def settings(db):
    from app.settings import Settings

    return Settings(db)


@pytest.fixture()
def mem(db):
    from app.memory.system import MemorySystem

    return MemorySystem(db)


@pytest.fixture(autouse=True)
def _tempdirs_under_tmp_path(monkeypatch, tmp_path):
    """测试里的 tempfile.mkdtemp 一律落到 pytest 的临时目录，跑完自动清。

    之前每个用例都在系统 TEMP 下建一个 novabot-* 目录，跑几十次就积几百个。
    """
    real = tempfile.mkdtemp

    def fake(*args, **kwargs):
        kwargs["dir"] = str(tmp_path)
        return real(*args, **kwargs)

    monkeypatch.setattr(tempfile, "mkdtemp", fake)
