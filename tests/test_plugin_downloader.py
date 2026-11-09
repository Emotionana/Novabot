"""插件下载：Release 资产过滤、Atom 兜底、下载源拼接。"""

from __future__ import annotations

from app.plugin_downloader import PluginDownloader, fallback_assets, parse_atom, parse_releases


def test_parse_snowluma_releases_only_windows():
    data = [{
        "tag_name": "v2.4.2", "name": "SnowLuma v2.4.2", "published_at": "2026-11-07T11:55:32Z",
        "draft": False, "prerelease": False,
        "assets": [
            {"name": "SnowLuma-v2.4.2-win-x64.zip", "size": 100, "browser_download_url": "https://x/a.zip", "digest": "sha256:abc"},
            {"name": "SnowLuma-v2.4.2-win-x64-lite.zip", "size": 50, "browser_download_url": "https://x/b.zip", "digest": ""},
            {"name": "SnowLuma-v2.4.2-linux-x64.tar.gz", "size": 99, "browser_download_url": "https://x/c.tar.gz", "digest": ""},
        ],
    }]
    rows = parse_releases(data, "snowluma")
    assert rows[0]["tag"] == "v2.4.2"
    assert [a["variant"] for a in rows[0]["assets"]] == ["full", "lite"]
    assert rows[0]["assets"][0]["sha256"] == "abc"
    assert len(rows[0]["assets"]) == 2


def test_atom_fallback_and_source_url():
    xml = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>v2.4.2</title><updated>2026-11-07T11:55:32Z</updated>
        <link rel="alternate" href="https://github.com/SnowLuma/SnowLuma/releases/tag/v2.4.2"/></entry>
    </feed>"""
    rows = parse_atom(xml, "snowluma")
    assert rows[0]["tag"] == "v2.4.2"
    assert len(rows[0]["assets"]) == 2
    assert PluginDownloader.source_url("direct", "https://github.com/x") == "https://github.com/x"
    assert PluginDownloader.source_url("ghproxy", "https://github.com/x") == "https://ghproxy.net/https://github.com/x"
    assert fallback_assets("napcat", "v4.18.42")[0]["name"] == "NapCat.Shell.zip"
