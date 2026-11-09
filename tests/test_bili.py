"""B 站识别与提取任务。"""

from __future__ import annotations

import asyncio

from app.bili import find_video_id, strip_html
from app.song_module import SongModule


def test_find_bvid_av_and_short_link():
    assert find_video_id("看看 https://www.bilibili.com/video/BV1ft4y1G7so") == {"bvid": "BV1ft4y1G7so"}
    assert find_video_id("av123456") == {"aid": "123456"}
    assert find_video_id("https://b23.tv/abc123") == {"short": "https://b23.tv/abc123"}
    assert find_video_id("没有视频") is None
    assert strip_html('<em class="keyword">起风了</em> &amp; 测试') == "起风了 & 测试"


def test_extract_only_job_does_not_need_engine(db, settings, monkeypatch):
    settings.set("module.song_enabled", True)
    svc = SongModule(db, settings)

    async def fake_run(job):
        job.update(status="done", stage="音频已提取", file="x.m4a")

    monkeypatch.setattr(svc, "_run", fake_run)

    async def run():
        r = svc.start(song_id="BV1ft4y1G7so", name="起风了", voice="", pitch=0, extract_only=True)
        await asyncio.sleep(0)
        return r

    r = asyncio.run(run())
    assert r["ok"] is True
    assert r["job"]["extract_only"] is True
