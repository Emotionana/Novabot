"""媒体入口：图片/表情/语音/视频解析 + 视觉/语音配置。"""

from __future__ import annotations

import asyncio
import base64
import time
from io import BytesIO

from PIL import Image

from app.gateway.bridge import parse_event
from app.media_service import MediaService
from app.models import InboundMessage, MediaItem


def _event(message: list, *, group=False):
    event = {
        "post_type": "message", "message_type": "group" if group else "private",
        "self_id": 10002, "user_id": 10001, "group_id": 1 if group else None,
        "time": int(time.time()),
        "sender": {"user_id": 10001, "nickname": "Test", "card": ""},
        "message": message,
    }
    return event


def test_bridge_parses_image_voice_video_sticker():
    msg = parse_event(_event([
        {"type": "image", "data": {"url": "https://example.com/a.png", "file": "a.png", "sub_type": "0"}},
        {"type": "image", "data": {"url": "https://example.com/s.png", "file": "s.png", "sub_type": "1"}},
        {"type": "record", "data": {"url": "https://example.com/v.amr", "file": "v.amr"}},
        {"type": "video", "data": {"url": "https://example.com/v.mp4", "file": "v.mp4"}},
        {"type": "mface", "data": {"url": "https://example.com/m.png", "file": "m.png"}},
        {"type": "file", "data": {"url": "https://example.com/a.txt", "name": "a.txt"}},
        {"type": "text", "data": {"text": "看看"}},
    ]))
    assert msg is not None
    assert [m.kind for m in msg.media] == ["image", "sticker", "voice", "video", "sticker", "file"]
    assert "看看" in msg.text


def test_media_service_uses_base64_and_vision(db, settings, monkeypatch):
    settings.set("media.vision_mode", "custom")
    settings.set("media.vision_base_url", "http://vision.test/v1")
    settings.set("media.vision_model", "vision-test")
    svc = MediaService(db, settings)
    calls = []

    async def fake_vision(data, mime, *, sticker=False):
        calls.append((len(data), mime, sticker))
        return "一张测试图片"

    monkeypatch.setattr(svc, "_vision", fake_vision)
    msg = InboundMessage(
        account_id="a1", chat_id="group:1", text="[图片]",
        media=[MediaItem(kind="image", url="base64://" + base64.b64encode(b"\xff\xd8\xff" + b"0" * 20).decode())],
    )
    note = asyncio.run(svc.enrich(msg))
    assert "一张测试图片" in note
    assert calls and calls[0][2] is False


def test_media_vision_chat_config_falls_back_to_llm_attrs(db, settings):
    settings.set("media.vision_mode", "chat")
    llm = type("Fake", (), {"base_url": "http://chat.test/v1", "model": "chat-model", "api_key": "k"})()
    svc = MediaService(db, settings, llm)
    assert svc._vision_config("chat") == ("http://chat.test/v1", "k", "chat-model")

def test_media_kind_toggles_skip_download(db, settings, monkeypatch):
    svc = MediaService(db, settings)
    calls = []

    async def fake_download(msg, item):
        calls.append(item.kind)
        return b"data", ""

    monkeypatch.setattr(svc, "_download", fake_download)
    settings.set("media.image_enabled", False)
    settings.set("media.voice_enabled", False)
    settings.set("media.video_enabled", False)
    msg = InboundMessage(
        account_id="a1", chat_id="private:1", text="[media]",
        media=[
            MediaItem(kind="image", url="base64://eA=="),
            MediaItem(kind="voice", url="base64://eA=="),
            MediaItem(kind="video", url="base64://eA=="),
        ],
    )
    assert asyncio.run(svc.enrich(msg)) == ""
    assert calls == []


def test_media_kind_defaults_enabled(db, settings):
    svc = MediaService(db, settings)
    assert svc.kind_enabled("image") is True
    assert svc.kind_enabled("voice") is True
    assert svc.kind_enabled("video") is True


def test_vision_image_is_resized_before_model():
    src = Image.new("RGB", (3200, 2000), "white")
    buf = BytesIO()
    src.save(buf, "JPEG", quality=95)
    data, mime = MediaService._normalize_image(buf.getvalue())
    assert mime == "image/jpeg"
    im = Image.open(BytesIO(data))
    assert max(im.size) <= 1600
    assert len(data) < 3 * 1024 * 1024


def test_dsh_vision_posts_image_to_agent(db, settings, monkeypatch):
    settings.set("media.vision_mode", "dsh")
    svc = MediaService(db, settings)
    svc.llm = type("FakeAgent", (), {"endpoint": "http://dsh.test/novabot/api/chat", "token": "t"})()
    seen = {}

    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"reply": "DSH 看到了"}

    class Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            seen["url"] = url
            seen["json"] = json
            seen["headers"] = headers
            return Resp()

    monkeypatch.setattr("app.media_service.httpx.AsyncClient", Client)
    out = asyncio.run(svc._dsh_vision(b"img", "image/jpeg", "描述"))
    assert out == "DSH 看到了"
    assert seen["url"] == "http://dsh.test/novabot/api/vision"
    assert seen["json"]["image"]["mimeType"] == "image/jpeg"
    assert seen["headers"]["Authorization"] == "Bearer t"


def test_image_and_video_vision_configs_are_separate(db, settings):
    settings.set("media.image_vision_mode", "custom")
    settings.set("media.image_vision_base_url", "http://image.test/v1")
    settings.set("media.image_vision_model", "image-model")
    settings.set("media.video_vision_mode", "custom")
    settings.set("media.video_vision_base_url", "http://video.test/v1")
    settings.set("media.video_vision_model", "video-model")
    svc = MediaService(db, settings)
    assert svc._vision_config("custom", "image") == ("http://image.test/v1", "", "image-model")
    assert svc._vision_config("custom", "video") == ("http://video.test/v1", "", "video-model")
    assert svc._vision_mode("image") == "custom"
    assert svc._vision_mode("video") == "custom"
