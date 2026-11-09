"""翻唱模块：网易云结果解析、音色库、引擎未配置时的明确报错。"""

from __future__ import annotations

from app.song_module import SongModule, parse_search


def test_parse_netease_search():
    data = {"result": {"songs": [{
        "id": 123, "name": "小幸运",
        "ar": [{"name": "田馥甄"}], "al": {"name": "我的少女时代"},
        "dt": 264000, "fee": 0,
    }]}}
    songs = parse_search(data)
    assert songs[0]["id"] == 123
    assert songs[0]["name"] == "小幸运"
    assert songs[0]["artists"] == "田馥甄"
    assert songs[0]["duration_sec"] == 264
    assert songs[0]["vip"] is False


def test_voice_import_and_engine_required(db, settings):
    svc = SongModule(db, settings)
    assert svc.status()["enabled"] is False
    assert svc.start(song_id=1, name="x", voice="", pitch=0)["ok"] is False

    r = svc.import_voice("demo.pth", b"\x80" + b"0" * 2048)
    assert r["ok"] is True
    assert svc.voices()[0]["id"] == "demo"
    assert svc.delete_voice("demo")["ok"] is True

    settings.set("module.song_enabled", True)
    r = svc.start(song_id=1, name="x", voice="demo", pitch=0)
    assert r["ok"] is False
    assert "换声引擎" in r["error"]
