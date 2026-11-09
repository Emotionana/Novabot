"""语音模块测试。

覆盖 design/05-语音.md 里的核心要求：
    参考音按长度/情绪/人格状态自动匹配、会话内粘住、按账号隔离
    引擎参数过滤、失败回退、音频统一 32k、合成日志、触发模式
"""

from __future__ import annotations

import io

import numpy as np
import pytest
import soundfile as sf

from app.adapters.tts.audio import normalize_wav
from app.adapters.tts.base import EngineCaps, TTSParams, VoiceError
from app.adapters.tts.gptsovits import GPTSoVITSEngine
from app.adapters.tts.pool import TTSPool
from app.adapters.tts.selector import VoiceSelector
from app.core.pipeline import Pipeline
from app.voice import VoiceService, load_ref, upsert_ref
from tests.helpers import ScriptedLLM, private_msg


def make_wav(seconds: float = 0.3, sr: int = 16000, channels: int = 1) -> bytes:
    data = np.zeros((int(sr * seconds), channels), dtype="float32")
    buf = io.BytesIO()
    sf.write(buf, data, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


class FakeEngine:
    name = "fake"

    def __init__(self, audio: bytes | None = None, *, fail: bool = False):
        self.audio = audio if audio is not None else make_wav()
        self.fail = fail
        self.calls: list[TTSParams] = []
        self.closed = False

    def caps(self) -> EngineCaps:
        return EngineCaps(name=self.name, params=frozenset())

    async def synth(self, params: TTSParams) -> bytes:
        self.calls.append(params)
        if self.fail:
            raise VoiceError("引擎炸了")
        return self.audio

    async def health(self) -> bool:
        return not self.fail

    async def close(self) -> None:
        self.closed = True


def add_ref(db, ref_id: str, **kw) -> str:
    data = {
        "id": ref_id,
        "persona_id": kw.pop("persona_id", ""),
        "name": kw.pop("name", ref_id),
        "engine": kw.pop("engine", "fake"),
        "audio_path": kw.pop("audio_path", f"/tmp/{ref_id}.wav"),
        "prompt_text": kw.pop("prompt_text", "参考文本"),
    }
    data.update(kw)
    return upsert_ref(db, data)


# ---------------------------------------------------------------------------
# 参考音匹配
# ---------------------------------------------------------------------------


def test_length_and_emotion_filter(db, settings):
    add_ref(db, "short", min_chars=1, max_chars=8, emotions=["anger"])
    add_ref(db, "long", min_chars=9, max_chars=60)

    sel = VoiceSelector(db, settings)

    short = sel.select("", "c1", "嗯")
    assert short.ref is not None and short.ref.id == "short"

    sel.reset()
    long_ = sel.select("", "c1", "这是一句足够长的话用来匹配长参考音")
    assert long_.ref is not None and long_.ref.id == "long"


def test_condition_uses_state_and_traits(db, settings):
    add_ref(db, "cold", condition={"state.mood": "< -0.3"})
    add_ref(db, "tsundere", condition={"trait.傲娇": "> 0.7"})

    sel = VoiceSelector(db, settings)
    from app.models import RuntimeState

    # 心情差 + 不傲娇 → 只能选 cold
    state = RuntimeState(mood=-0.6)
    sel_ = sel.select("", "c2", "随便说一句", state=state, traits={"傲娇": 0.1})
    assert sel_.ref is not None and sel_.ref.id == "cold"

    sel.reset()
    state2 = RuntimeState(mood=0.2)
    sel_ = sel.select("", "c2", "随便说一句", state=state2, traits={"傲娇": 0.9})
    assert sel_.ref is not None and sel_.ref.id == "tsundere"


def test_sticky_keeps_then_switches_on_length(db, settings):
    add_ref(db, "a", min_chars=1, max_chars=100)
    add_ref(db, "b", min_chars=1, max_chars=100, weight=0.01)
    sel = VoiceSelector(db, settings)

    first = sel.select("", "c3", "第一句话")
    second = sel.select("", "c3", "第二句话")
    assert first.ref.id == second.ref.id, "同一会话应该粘住"

    # 长度换到只有 b 能满足的区间
    sel.reset()
    add_ref(db, "a", min_chars=1, max_chars=4)
    sel.select("", "c4", "短句")
    switched = sel.select("", "c4", "这句话明显超过了四个字")
    assert switched.ref.id == "b"


def test_sticky_account_isolation(db, settings):
    add_ref(db, "r-a", min_chars=1, max_chars=100, account_id="acc-a")
    add_ref(db, "r-b", min_chars=1, max_chars=100, account_id="acc-b")
    sel = VoiceSelector(db, settings)

    sel.select("", "group:1", "你好", account_id="acc-a")
    sel.select("", "group:1", "你好", account_id="acc-b")

    assert sel.current("group:1", account_id="acc-a") == "r-a"
    assert sel.current("group:1", account_id="acc-b") == "r-b"

    sel.reset("group:1", account_id="acc-a")
    assert sel.current("group:1", account_id="acc-a") == ""
    assert sel.current("group:1", account_id="acc-b") == "r-b", "重置 A 不能影响 B"


def test_global_ref_works_without_persona(db, settings):
    add_ref(db, "global-ref", persona_id="")
    sel = VoiceSelector(db, settings)

    chosen = sel.select("persona-x", "c5", "你好呀")

    assert chosen.ref is not None and chosen.ref.id == "global-ref"


# ---------------------------------------------------------------------------
# 引擎与音频
# ---------------------------------------------------------------------------


def test_engine_drops_unsupported_params():
    engine = GPTSoVITSEngine(url="http://127.0.0.1:9880", defaults={"top_k": 5})
    params = TTSParams(
        text="你好",
        ref_audio="/tmp/a.wav",
        engine="gptsovits",
        options={"temperature": 0.8, "speed": 1.2, "whatever": 1},
    )

    q = engine._query(params)

    assert q["top_k"] == 5
    assert q["temperature"] == 0.8
    assert q["speed_factor"] == 1.2
    assert "whatever" not in q
    assert q["media_type"] == "wav"


@pytest.mark.asyncio
async def test_pool_falls_back_and_can_disable():
    bad = FakeEngine(fail=True)
    good = FakeEngine()
    pool = TTSPool({"bad": bad, "good": good}, fallback=["good"])

    audio, used = await pool.synth(TTSParams(text="x", ref_audio="a", engine="bad"))
    assert used == "good" and audio

    pool.allow_fallback = False
    with pytest.raises(VoiceError):
        await pool.synth(TTSParams(text="x", ref_audio="a", engine="bad"))


def test_normalize_to_32k_mono():
    stereo = make_wav(seconds=0.4, sr=44100, channels=2)

    wav, duration = normalize_wav(stereo)
    data, rate = sf.read(io.BytesIO(wav), always_2d=True)

    assert rate == 32000
    assert data.shape[1] == 1
    assert abs(duration - 0.4) < 0.05


# ---------------------------------------------------------------------------
# 服务：触发、合成、日志、预览
# ---------------------------------------------------------------------------


def make_service(db, settings, engine=None):
    engine = engine or FakeEngine()
    pool = TTSPool({"fake": engine})
    return VoiceService(db, None, _Cfg(), settings, pool=pool), engine


class _Cfg:
    class voice:  # noqa: N801
        enabled = True
        default_engine = "fake"
        fallback = []
        engines = {}
        refs = []


def test_trigger_modes(settings, db):
    service, _ = make_service(db, settings)

    assert service.should_speak(mode="never", is_owner=True, user_text="", chat_id="c") is False
    assert service.should_speak(mode="always", is_owner=False, user_text="", chat_id="c") is True
    assert service.should_speak(mode="boss_only", is_owner=True, user_text="", chat_id="c") is True
    assert service.should_speak(mode="boss_only", is_owner=False, user_text="", chat_id="c") is False
    assert service.should_speak(mode="asked", is_owner=False, user_text="发条语音", chat_id="c") is True
    assert service.should_speak(mode="asked", is_owner=False, user_text="在吗", chat_id="c") is False


@pytest.mark.asyncio
async def test_speak_returns_wav_and_logs(db, settings):
    settings.set("voice.enabled", True)
    settings.set("voice.trigger_mode", "always")
    settings.set("voice.min_chars", 1)
    settings.set("voice.max_chars", 100)
    settings.set("voice.log_enabled", True)
    add_ref(db, "r1", min_chars=1, max_chars=100, account_id="acc")

    service, engine = make_service(db, settings)

    audio = await service.speak(
        account_id="acc", chat_id="c1", sender_qq="1", text="你好呀，今天怎么样"
    )

    assert audio and audio.startswith(b"RIFF")
    assert len(engine.calls) == 1
    row = db.query_one("SELECT * FROM voice_logs ORDER BY id DESC LIMIT 1")
    assert row["ref_id"] == "r1"
    assert row["ok"] == 1
    assert row["duration"] > 0


@pytest.mark.asyncio
async def test_speak_failure_returns_none_and_logs_failure(db, settings):
    settings.set("voice.enabled", True)
    settings.set("voice.trigger_mode", "always")
    settings.set("voice.min_chars", 1)
    settings.set("voice.log_enabled", True)
    add_ref(db, "r1", min_chars=1, max_chars=100, account_id="acc")

    service, _ = make_service(db, settings, engine=FakeEngine(fail=True))

    audio = await service.speak(account_id="acc", chat_id="c1", sender_qq="1", text="你好呀")

    assert audio is None
    row = db.query_one("SELECT * FROM voice_logs ORDER BY id DESC LIMIT 1")
    assert row["ok"] == 0


@pytest.mark.asyncio
async def test_speak_respects_min_chars_and_trigger(db, settings):
    settings.set("voice.enabled", True)
    settings.set("voice.trigger_mode", "never")
    settings.set("voice.min_chars", 5)
    add_ref(db, "r1", min_chars=1, max_chars=100)
    service, engine = make_service(db, settings)

    assert await service.speak(account_id="a", chat_id="c", sender_qq="1", text="太长了吧") is None
    assert engine.calls == []


@pytest.mark.asyncio
async def test_preview_ignores_trigger(db, settings):
    settings.set("voice.enabled", False)
    settings.set("voice.trigger_mode", "never")
    add_ref(db, "r1", min_chars=0, max_chars=999)

    service, _ = make_service(db, settings)
    wav, meta = await service.preview(text="试听一句")

    assert wav.startswith(b"RIFF")
    assert meta["ref_id"] == "r1"
    assert meta["engine"] == "fake"


def test_load_ref_roundtrip(db):
    add_ref(db, "r9", params={"seed": -1, "top_k": 5}, emotions=["joy"])

    ref = load_ref(db, "r9")

    assert ref is not None
    assert ref.params["top_k"] == 5
    assert ref.emotions == ["joy"]


# ---------------------------------------------------------------------------
# 接进 Pipeline
# ---------------------------------------------------------------------------


class StubVoice:
    def __init__(self, audio: bytes | None, delay: float = 0.0):
        self.audio = audio
        self.delay = delay
        self.calls: list[dict] = []

    async def speak(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            import asyncio

            await asyncio.sleep(self.delay)
        return self.audio


@pytest.mark.asyncio
async def test_pipeline_attaches_voice_and_keeps_text(db, mem, settings):
    wav = make_wav()
    voice = StubVoice(wav)
    settings.set("reply.cooldown_sec", 0)
    settings.set("reply.max_per_min", 0)
    pipe = Pipeline(
        settings=settings, memory=mem, llm=ScriptedLLM("好的"), voice=voice
    )

    result = await pipe.handle(private_msg("在吗"))

    assert result.replied
    assert result.messages[0].voice == wav
    # 正文保留：语音发失败时网关要能补发文字
    assert result.messages[0].text == "好的"
    assert voice.calls and voice.calls[0]["text"] == "好的"


@pytest.mark.asyncio
async def test_pipeline_falls_back_to_text_when_voice_fails(db, mem, settings):
    voice = StubVoice(None)
    settings.set("reply.cooldown_sec", 0)
    settings.set("reply.max_per_min", 0)
    pipe = Pipeline(
        settings=settings, memory=mem, llm=ScriptedLLM("好的"), voice=voice
    )

    result = await pipe.handle(private_msg("在吗"))

    assert result.messages[0].voice is None
    assert result.messages[0].text == "好的"


def test_emotion_specific_ref_is_preferred(db, settings):
    """有情绪标签的参考音优先，通用参考只是兜底。"""
    add_ref(db, "generic", min_chars=0, max_chars=99)
    add_ref(db, "angry", min_chars=0, max_chars=99, emotions=["anger"])
    sel = VoiceSelector(db, settings)

    chosen = sel.select("", "c-emo", "随便一句", emotion="anger")

    assert chosen.ref.id == "angry"


def test_condition_specific_ref_is_preferred(db, settings):
    """条件命中的参考音优先于无条件兜底。"""
    from app.models import RuntimeState

    add_ref(db, "calm", min_chars=0, max_chars=99)
    add_ref(db, "cold", min_chars=0, max_chars=99, condition={"state.mood": "< -0.3"})
    sel = VoiceSelector(db, settings)

    chosen = sel.select("", "c-cond", "随便一句", state=RuntimeState(mood=-0.6))

    assert chosen.ref.id == "cold"


def test_question_tag_is_preferred(db, settings):
    """问句优先用打了「疑问」标签的参考音。"""
    add_ref(db, "plain", min_chars=0, max_chars=99)
    add_ref(db, "question", min_chars=0, max_chars=99, tags=["疑问"])
    sel = VoiceSelector(db, settings)

    chosen = sel.select("", "c-tag", "你吃饭了吗？")

    assert chosen.ref.id == "question"


@pytest.mark.asyncio
async def test_gateway_falls_back_to_text_when_voice_fails(settings):
    """语音发不出去时，补发文字，别让用户什么都收不到。"""
    from types import SimpleNamespace

    from app.gateway.manager import GatewayManager
    from app.gateway.onebot import OneBotError
    from app.models import OutboundMessage

    class FakeClient:
        connected = True

        def __init__(self):
            self.sent = []

        async def send(self, ob):
            if getattr(ob, "voice", None):
                raise OneBotError("语音通道挂了")
            self.sent.append(ob)

    client = FakeClient()
    rt = SimpleNamespace(id="acc", client=client, sent=0, errors=0, last_error="")
    gw = GatewayManager(settings)
    result = SimpleNamespace(
        messages=[OutboundMessage(chat_id="private:1", text="好的", voice=b"RIFF")]
    )

    await gw._send_result(rt, result, "private:1")

    assert [o.text for o in client.sent] == ["好的"]
    assert rt.sent == 1


@pytest.mark.asyncio
async def test_voice_budget_cuts_slow_synthesis(db, mem, settings):
    """TTS 超过预算就只发文字，别把整条回复拖住。"""
    settings.set("reply.cooldown_sec", 0)
    settings.set("reply.max_per_min", 0)
    settings.set("voice.reply_budget_sec", 0.1)
    voice = StubVoice(make_wav(), delay=0.3)
    pipe = Pipeline(settings=settings, memory=mem, llm=ScriptedLLM("好的"), voice=voice)

    result = await pipe.handle(private_msg("在吗"))

    assert result.replied
    assert result.messages[0].voice is None
    assert result.messages[0].text == "好的"

def test_convert_rate_and_explicit_ask(settings, db, monkeypatch):
    service, _ = make_service(db, settings)
    settings.set("voice.reply_mode", "both")
    settings.set("voice.convert_rate", 0)
    assert service.should_convert() is False
    settings.set("voice.convert_rate", 100)
    assert service.should_convert() is True
    settings.set("voice.convert_rate", 50)
    monkeypatch.setattr("app.voice.random.random", lambda: 0.4)
    assert service.should_convert() is True
    monkeypatch.setattr("app.voice.random.random", lambda: 0.6)
    assert service.should_convert() is False
    settings.set("voice.convert_rate", 0)
    assert service.should_convert(force=True) is False


class _StubVoice:
    def __init__(self, mode="both", audio=b"RIFF"):
        self.mode = mode
        self.audio = audio
        self.spoken = []

    def reply_mode(self):
        return self.mode

    def is_explicit_ask(self, user_text):
        return False

    def should_convert(self, force=False):
        return True

    async def speak(self, **kwargs):
        self.spoken.append(kwargs)
        return self.audio


def _pipeline_for_voice(settings, voice):
    from app.core.pipeline import Pipeline

    p = object.__new__(Pipeline)
    p.settings = settings
    p.voice = voice
    return p


def _voice_result(*texts):
    from types import SimpleNamespace

    from app.models import OutboundMessage

    return SimpleNamespace(messages=[OutboundMessage(chat_id="c", text=x) for x in texts])


@pytest.mark.asyncio
async def test_attach_voice_both_keeps_text(settings, db):
    from types import SimpleNamespace

    settings.set("voice.reply_budget_sec", 10)
    voice = _StubVoice("both")
    p = _pipeline_for_voice(settings, voice)
    result = _voice_result("第一句", "第二句")
    msg = SimpleNamespace(account_id="a", chat_id="c", sender_qq="1", text="在吗")
    await p._attach_voice(msg, "第一句第二句", result, is_owner=False)
    assert result.messages[0].voice == b"RIFF"
    assert [m.text for m in result.messages] == ["第一句", "第二句"]
    assert result.messages[0].voice_only is False


@pytest.mark.asyncio
async def test_attach_voice_only_clears_text_and_keeps_fallback(settings, db):
    from types import SimpleNamespace

    settings.set("voice.reply_budget_sec", 10)
    p = _pipeline_for_voice(settings, _StubVoice("voice"))
    result = _voice_result("第一句", "第二句")
    msg = SimpleNamespace(account_id="a", chat_id="c", sender_qq="1", text="在吗")
    await p._attach_voice(msg, "第一句第二句", result, is_owner=False)
    assert all(m.text == "" for m in result.messages)
    assert result.messages[0].voice == b"RIFF"
    assert result.messages[0].voice_only is True
    assert result.messages[0].voice_fallback_text == "第一句\n第二句"


@pytest.mark.asyncio
async def test_attach_voice_mixed_only_converts_one(settings, db, monkeypatch):
    from types import SimpleNamespace

    settings.set("voice.reply_budget_sec", 10)
    p = _pipeline_for_voice(settings, _StubVoice("mixed"))
    result = _voice_result("第一句", "第二句")
    monkeypatch.setattr("app.core.pipeline.random.choice", lambda items: items[1])
    msg = SimpleNamespace(account_id="a", chat_id="c", sender_qq="1", text="在吗")
    await p._attach_voice(msg, "第一句第二句", result, is_owner=False)
    assert result.messages[0].text == "第一句"
    assert result.messages[1].text == ""
    assert result.messages[1].voice == b"RIFF"
    assert result.messages[1].voice_fallback_text == "第二句"


@pytest.mark.asyncio
async def test_attach_voice_failure_keeps_text(settings, db):
    from types import SimpleNamespace

    settings.set("voice.reply_budget_sec", 10)
    p = _pipeline_for_voice(settings, _StubVoice("voice", audio=None))
    result = _voice_result("第一句")
    msg = SimpleNamespace(account_id="a", chat_id="c", sender_qq="1", text="在吗")
    await p._attach_voice(msg, "第一句", result, is_owner=False)
    assert result.messages[0].text == "第一句"
    assert result.messages[0].voice is None

def test_sync_refs_does_not_overwrite_user_engine(db):
    from types import SimpleNamespace

    from app.voice import sync_refs_from_config

    upsert_ref(db, {
        "id": "r1", "account_id": "acc-a", "name": "A", "engine": "gptsovits-9881",
        "audio_path": "/tmp/a.wav", "min_chars": 1, "max_chars": 40,
    })
    cfg = SimpleNamespace(voice=SimpleNamespace(
        refs=[{"id": "r1", "name": "旧配置", "engine": "gptsovits", "audio_path": "/tmp/old.wav"}],
        default_engine="gptsovits",
    ))
    sync_refs_from_config(db, cfg, "acc-a")
    row = db.query_one("SELECT engine, name FROM voice_refs WHERE id='r1'")
    assert row["engine"] == "gptsovits-9881"
    assert row["name"] == "A"


def test_seed_engine_does_not_readd_when_table_has_rows(db):
    from types import SimpleNamespace

    from app.voice_engines import seed_from_config

    db.execute(
        "INSERT INTO voice_engines (id, name, type, url, defaults, enabled, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ["gptsovits-9881", "GPT-SoVITS", "gptsovits", "http://127.0.0.1:9881", "{}", 1, 1.0, 1.0],
    )
    cfg = SimpleNamespace(voice=SimpleNamespace(engines={"gptsovits": {"url": "http://127.0.0.1:9880"}}))
    assert seed_from_config(db, cfg) == 0
    assert db.query_one("SELECT 1 FROM voice_engines WHERE id='gptsovits'") is None

