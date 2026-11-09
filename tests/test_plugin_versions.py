"""插件版本列表与切换校验。"""

from __future__ import annotations

import pytest

from app.plugin_downloader import PluginDownloader
from app.settings import Settings


@pytest.mark.asyncio
async def test_switch_rejects_unknown_version(db, settings):
    svc = PluginDownloader(db, Settings(db))
    r = await svc.switch("snowluma", "9.9.9")
    assert r["ok"] is False
    assert "没有安装" in r["error"]


@pytest.mark.asyncio
async def test_switch_rejects_non_snowluma(db, settings):
    svc = PluginDownloader(db, Settings(db))
    r = await svc.switch("napcat", "2.0.0")
    assert r["ok"] is False
    assert "SnowLuma" in r["error"]


def test_installed_versions_can_be_listed(db, settings):
    svc = PluginDownloader(db, Settings(db))
    rows = svc.installed_info("snowluma")
    assert isinstance(rows, list)
    for row in rows:
        assert {"version", "path", "current"} <= set(row)
