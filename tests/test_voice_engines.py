"""语音引擎存储、适配器和引擎池。"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import httpx
import pytest

from app.adapters.tts.base import TTSParams
from app.adapters.tts.http import HTTPEngine
from app.adapters.tts.openai import OpenAIEngine
from app.voice_engines import build_pool_from_db, list_engines, save_engine


def cfg():
    return SimpleNamespace(voice=SimpleNamespace(engines={}, fallback=[], allow_fallback=True))


def test_engine_crud_masks_key(db):
    row = save_engine(db, {
        "id": "fish", "name": "Fish", "type": "openai",
        "url": "http://127.0.0.1:8080", "api_key": "secret",
        "model": "fish", "voice": "default", "defaults": {"speed": 1.1},
    })
    assert row["api_key"] == "••••••"
    assert row["api_key_set"] is True
    assert list_engines(db)[0]["id"] == "fish"

    # 留空/打码保存不覆盖真 key
    save_engine(db, {"id": "fish", "name": "Fish", "type": "openai",
                     "url": "http://127.0.0.1:8080", "api_key": ""})
    assert db.scalar("SELECT api_key FROM voice_engines WHERE id='fish'") == "secret"

    pool = build_pool_from_db(db, cfg())
    assert pool.names() == ["fish"]
    assert pool.get("fish") is not None


def test_openai_request_body():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"RIFF" + b"0" * 400)

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        engine = OpenAIEngine(base_url="http://tts.local/v1", api_key="k",
                              model="m", voice="v", client=client)
        out = await engine.synth(TTSParams(text="你好"))
        await engine.close()
        return out

    out = pytest.importorskip("asyncio").run(run())
    assert out[:4] == b"RIFF"
    assert seen["path"] == "/v1/audio/speech"
    assert seen["body"]["input"] == "你好"
    assert seen["body"]["model"] == "m"


def test_http_engine_json_base64():
    wav = b"RIFF" + b"0" * 400
    b64 = base64.b64encode(wav).decode()

    def handler(request: httpx.Request):
        return httpx.Response(200, json={"data": b64})

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        engine = HTTPEngine(url="http://tts.local/tts", client=client,
                            defaults={"headers": {"x-test": "1"}})
        out = await engine.synth(TTSParams(text="hi"))
        await engine.close()
        return out

    out = pytest.importorskip("asyncio").run(run())
    assert out[:4] == b"RIFF"
