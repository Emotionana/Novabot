"""语音服务：把「一条要发出去的回复」变成「一段能发的语音」。

流程（design/05-语音.md）：
    触发判断 → 取人格快照 → 判情绪 → 选参考音（长度/情绪/状态）→ 合成
    → 统一 32k 单声道 → 时长检查 → 记账 → 交给发送层

失败一律返回 None（上层继续发文字）。语音是增强，不是必经之路。
"""

from __future__ import annotations

import contextlib
import json
import logging
import random
import time

from .adapters.tts.audio import normalize_wav
from .adapters.tts.base import TTSParams, VoiceError
from .adapters.tts.gptsovits import GPTSoVITSEngine
from .adapters.tts.pool import TTSPool
from .adapters.tts.selector import Selection, VoiceSelector
from .db import Database
from .voice_engines import build_pool_from_db

log = logging.getLogger(__name__)

MAX_SECONDS = 60.0  # QQ 语音上限
ENGINE_TYPES = {"gptsovits", "gpt-sovits", "gpt_sovits"}

# "对方要求时"：说这些话就发语音
ASK_WORDS = (
    "语音",
    "发条语音",
    "说句话",
    "听听你的声音",
    "听听声音",
    "念一下",
    "用声音",
)


def build_pool(cfg) -> TTSPool:
    """按 [voice.engines.xxx] 造引擎池。"""
    engines = {}
    for name, spec in (getattr(cfg.voice, "engines", None) or {}).items():
        if not isinstance(spec, dict):
            continue
        kind = str(spec.get("type") or "gptsovits").strip().lower()
        if kind in ENGINE_TYPES:
            defaults = {
                k: v
                for k, v in spec.items()
                if k not in ("type", "url", "timeout")
            }
            engines[name] = GPTSoVITSEngine(
                url=str(spec.get("url") or ""),
                timeout=float(spec.get("timeout") or 120),
                defaults=defaults,
            )
        else:
            log.warning("语音引擎「%s」类型 %s 暂不支持", name, kind)
    return TTSPool(engines, fallback=list(getattr(cfg.voice, "fallback", None) or []))


def upsert_ref(db: Database, ref: dict, *, default_engine: str = "") -> str:
    """一条参考音写入/更新。返回 id。配置同步和接口都用它。"""
    ref_id = str(ref.get("id") or "").strip()
    audio = str(ref.get("audio_path") or ref.get("audio") or "").strip()
    if not ref_id or not audio:
        raise ValueError("参考音必须有 id 和 audio_path")

    now = time.time()
    db.execute(
        """
        INSERT INTO voice_refs
          (id, account_id, persona_id, name, engine, audio_path, duration, prompt_text,
           params, weight, tags, min_chars, max_chars, emotions, condition,
           enabled, note, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          account_id=COALESCE(NULLIF(excluded.account_id,''), voice_refs.account_id),
          persona_id=excluded.persona_id, name=excluded.name,
          engine=excluded.engine, audio_path=excluded.audio_path,
          prompt_text=excluded.prompt_text, params=excluded.params,
          weight=excluded.weight, tags=excluded.tags,
          min_chars=excluded.min_chars, max_chars=excluded.max_chars,
          emotions=excluded.emotions, condition=excluded.condition,
          enabled=excluded.enabled, note=excluded.note, updated_at=excluded.updated_at
        """,
        [
            ref_id,
            str(ref.get("account_id") or ""),
            str(ref.get("persona_id") or ""),
            str(ref.get("name") or ref_id),
            str(ref.get("engine") or default_engine or "gptsovits"),
            audio,
            float(ref.get("duration") or 0.0),
            str(ref.get("prompt_text") or ref.get("prompt") or ""),
            json.dumps(ref.get("params") or {}, ensure_ascii=False),
            float(ref.get("weight") or 1.0),
            json.dumps(ref.get("tags") or [], ensure_ascii=False),
            int(ref.get("min_chars") or 0),
            int(ref.get("max_chars") or 9999),
            json.dumps(ref.get("emotions") or [], ensure_ascii=False),
            json.dumps(ref["condition"], ensure_ascii=False) if ref.get("condition") else None,
            1 if ref.get("enabled", True) else 0,
            str(ref.get("note") or ""),
            now,
            now,
        ],
    )
    return ref_id


def sync_refs_from_config(db: Database, cfg, default_account_id: str = "") -> int:
    """把配置里的 [[voice.refs]] 同步进库。已存在的按 id 更新。"""
    refs = getattr(cfg.voice, "refs", None) or []
    n = 0
    for ref in refs:
        try:
            data = dict(ref)
            if db.query_one("SELECT 1 FROM voice_refs WHERE id = ?", [data.get("id")]):
                continue
            if default_account_id and not data.get("account_id"):
                data["account_id"] = default_account_id
            upsert_ref(db, data, default_engine=cfg.voice.default_engine or "")
            n += 1
        except Exception as e:  # noqa: BLE001
            log.warning("同步参考音失败（%s）：%s", ref.get("id"), e)
    if n:
        log.info("参考音已同步：%d 条", n)
    return n


def load_ref(db: Database, ref_id: str, account_id: str = ""):
    """按 id 取一条参考音，转成 VoiceRef。"""
    from .models import VoiceRef

    if account_id:
        row = db.query_one("SELECT * FROM voice_refs WHERE id = ? AND account_id = ?", [ref_id, account_id])
    else:
        row = db.query_one("SELECT * FROM voice_refs WHERE id = ?", [ref_id])
    if row is None:
        return None
    return VoiceRef(
        id=row["id"],
        account_id=row["account_id"] or "",
        persona_id=row["persona_id"],
        name=row["name"],
        engine=row["engine"],
        audio_path=row["audio_path"],
        duration=row["duration"] or 0.0,
        prompt_text=row["prompt_text"] or "",
        params=json.loads(row["params"] or "{}"),
        weight=row["weight"] or 1.0,
        tags=json.loads(row["tags"] or "[]"),
        min_chars=row["min_chars"] or 0,
        max_chars=row["max_chars"] or 9999,
        emotions=json.loads(row["emotions"] or "[]"),
        condition=json.loads(row["condition"]) if row["condition"] else None,
        enabled=bool(row["enabled"]),
    )


class VoiceService:
    """一个账号一份。selector 的粘住状态和参数都按账号隔离。"""

    def __init__(self, db: Database, persona, cfg, settings, *, pool: TTSPool | None = None):
        self.db = db
        self.persona = persona
        self.cfg = cfg
        self.settings = settings
        self.account_id = getattr(settings, "account_id", "") or ""
        with contextlib.suppress(Exception):
            self.settings.get("voice.engine_url")
        self.pool = pool if pool is not None else build_pool_from_db(db, cfg, self.account_id)
        self.selector = VoiceSelector(db, settings)
        self._amygdala = None

    # -- 状态 ------------------------------------------------------------

    def enabled(self) -> bool:
        try:
            return bool(self.settings.get("voice.enabled")) and bool(self.pool.names())
        except Exception:  # noqa: BLE001
            return False

    @property
    def amygdala(self):
        if self._amygdala is None:
            from .memory.amygdala import Amygdala

            self._amygdala = Amygdala(self.db)
        return self._amygdala

    # -- 主入口 ----------------------------------------------------------

    def reply_mode(self) -> str:
        try:
            mode = str(self.settings.get("voice.reply_mode") or "both").lower()
        except Exception:  # noqa: BLE001
            mode = "both"
        return mode if mode in {"both", "voice", "mixed"} else "both"

    def convert_rate(self) -> int:
        try:
            return max(0, min(100, int(float(self.settings.get("voice.convert_rate") or 0))))
        except Exception:  # noqa: BLE001
            return 100

    def is_explicit_ask(self, user_text: str) -> bool:
        return any(w in (user_text or "") for w in ASK_WORDS)

    def should_convert(self, *, force: bool = False) -> bool:
        rate = self.convert_rate()
        if rate <= 0:
            return False
        if force or rate >= 100:
            return True
        return random.random() * 100 < rate

    def should_speak(
        self,
        *,
        mode: str,
        is_owner: bool,
        user_text: str,
        chat_id: str,
    ) -> bool:
        mode = (mode or "never").lower()
        if mode == "never":
            return False
        if mode == "always":
            return True
        if mode == "boss_only":
            return bool(is_owner)
        if mode == "asked":
            return any(w in (user_text or "") for w in ASK_WORDS)
        if mode == "probability":
            # 旧模式已废弃；概率统一走 voice.convert_rate
            return False

    def pick_ref(
        self,
        *,
        account_id: str,
        chat_id: str,
        text: str,
        user_text: str = "",
        is_owner: bool = False,
        emotion: str | None = None,
    ) -> Selection:
        """只选参考音，不合成。调试和接口预览都用它。"""
        snapshot = None
        if self.persona is not None:
            try:
                snapshot = self.persona.snapshot(
                    chat_id=chat_id, sender_qq="", text=user_text, account_id=account_id
                )
            except Exception as e:  # noqa: BLE001
                log.debug("取人格快照失败：%s", e)

        if emotion is None:
            try:
                emotion = self.amygdala.rule_judge(text).emotion
            except Exception:  # noqa: BLE001
                emotion = None

        persona_id = snapshot.persona_id if snapshot else ""
        return self.selector.select(
            persona_id,
            chat_id,
            text,
            emotion=emotion,
            state=snapshot.state if snapshot else None,
            traits=snapshot.traits if snapshot else None,
            style=snapshot.style if snapshot else None,
            account_id=account_id,
        )

    async def speak(
        self,
        *,
        account_id: str,
        chat_id: str,
        sender_qq: str,
        text: str,
        user_text: str = "",
        is_owner: bool = False,
    ) -> bytes | None:
        """要发语音就返回 32k wav 字节；不发或失败返回 None。"""
        if not self.enabled():
            return None

        try:
            mode = self.settings.get("voice.trigger_mode") or "never"
            min_chars = int(self.settings.get("voice.min_chars") or 0)
            max_chars = int(self.settings.get("voice.max_chars") or 60)
            log_enabled = bool(self.settings.get("voice.log_enabled"))
        except Exception as e:  # noqa: BLE001
            log.warning("读语音参数失败：%s", e)
            return None

        text = (text or "").strip()
        if len(text) < min_chars:
            return None
        if not self.should_speak(
            mode=str(mode), is_owner=is_owner, user_text=user_text, chat_id=chat_id
        ):
            return None

        # 长句先截断再合成，别等合成完才发现发不出去（design §九）
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "…"

        selection = self.pick_ref(
            account_id=account_id,
            chat_id=chat_id,
            text=text,
            user_text=user_text,
            is_owner=is_owner,
        )
        ref = selection.ref
        if ref is None:
            log.info("没有可用参考音：%s", selection.reason)
            return None

        engine = ref.engine or self.settings.get("voice.default_engine") or (
            self.pool.names()[0] if self.pool.names() else ""
        )
        self.pool.allow_fallback = bool(self.settings.get("voice.allow_fallback"))

        params = TTSParams(
            text=text,
            ref_audio=ref.audio_path,
            prompt_text=ref.prompt_text or "",
            engine=engine,
            options=dict(ref.params or {}),
        )

        started = time.time()
        try:
            audio, used = await self.pool.synth(params)
            wav, duration = normalize_wav(audio)
        except VoiceError as e:
            log.warning("语音合成失败（发文字）：%s", e)
            if log_enabled:
                self._log(account_id, ref, engine, text, duration=0.0, ok=False, error=str(e))
            return None
        except Exception as e:  # noqa: BLE001
            log.exception("语音合成异常（发文字）")
            if log_enabled:
                self._log(account_id, ref, engine, text, duration=0.0, ok=False, error=str(e))
            return None

        if duration > MAX_SECONDS:
            log.warning("语音 %.1fs 超过 %.0fs 上限，改发文字", duration, MAX_SECONDS)
            if log_enabled:
                self._log(
                    account_id, ref, used, text, duration=duration, ok=False,
                    error="超过时长上限",
                )
            return None

        log.info(
            "语音 OK：参考音「%s」引擎 %s %.2fs（选它是因为：%s）",
            ref.name,
            used,
            time.time() - started,
            selection.reason,
        )
        if log_enabled:
            self._log(account_id, ref, used, text, duration=duration, ok=True, error="")
        return wav

    async def preview(
        self,
        *,
        text: str,
        account_id: str = "",
        chat_id: str = "preview",
        ref_id: str = "",
        emotion: str | None = None,
    ) -> tuple[bytes, dict]:
        """手动试听：不管开关和触发规则，直接合成一段。"""
        text = (text or "").strip()
        if not text:
            raise VoiceError("没有文本")

        try:
            log_on = bool(self.settings.get("voice.log_enabled"))
        except Exception:  # noqa: BLE001
            log_on = True

        reason = "指定参考音"
        if ref_id:
            ref = load_ref(self.db, ref_id, account_id)
        else:
            selection = self.pick_ref(
                account_id=account_id, chat_id=chat_id, text=text,
                user_text=text, emotion=emotion,
            )
            ref = selection.ref
            reason = selection.reason
        if ref is None:
            raise VoiceError("没有可用参考音")

        engine = ref.engine or self.settings.get("voice.default_engine") or (
            self.pool.names()[0] if self.pool.names() else ""
        )
        try:
            audio, used = await self.pool.synth(
                TTSParams(
                    text=text,
                    ref_audio=ref.audio_path,
                    prompt_text=ref.prompt_text or "",
                    engine=engine,
                    options=dict(ref.params or {}),
                )
            )
            wav, duration = normalize_wav(audio)
        except VoiceError as e:
            if log_on:
                self._log(account_id, ref, engine, text, duration=0.0, ok=False, error=str(e))
            raise
        if duration > MAX_SECONDS:
            if log_on:
                self._log(account_id, ref, used, text, duration=duration, ok=False,
                          error="超过时长上限")
            raise VoiceError(f"合成出来 {duration:.0f}s，超过上限")
        if log_on:
            self._log(account_id, ref, used, text, duration=duration, ok=True, error="")
        return wav, {
            "ref": ref.name,
            "ref_id": ref.id,
            "engine": used,
            "duration": round(duration, 2),
            "reason": reason,
        }

    # -- 维护 ------------------------------------------------------------

    async def reload_engines(self) -> list[str]:
        """面板改完引擎后重建 pool；旧 pool 的 HTTP client 关掉。"""
        old = self.pool
        self.pool = build_pool_from_db(self.db, self.cfg, self.account_id)
        try:
            await old.close()
        except Exception:  # noqa: BLE001
            log.debug("关闭旧语音池失败", exc_info=True)
        return self.pool.names()

    def _log(self, account_id, ref, engine, text, *, duration, ok, error) -> None:
        try:
            self.db.execute(
                "INSERT INTO voice_logs "
                "(account_id, persona_id, ref_id, engine, text, text_len, params, duration, ok, error, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [
                    account_id or (getattr(ref, "account_id", "") if ref else ""),
                    ref.persona_id if ref else "",
                    ref.id if ref else "",
                    engine or "",
                    text[:500],
                    len(text),
                    json.dumps(getattr(ref, "params", {}) or {}, ensure_ascii=False),
                    duration,
                    1 if ok else 0,
                    (error or "")[:300],
                    time.time(),
                ],
            )
        except Exception as e:  # noqa: BLE001
            log.debug("写语音日志失败：%s", e)

    async def close(self) -> None:
        await self.pool.close()


def _hash_ratio(s: str) -> float:
    """确定性随机：同一条消息判两次结果一致。"""
    h = 0
    for ch in s:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h / 0xFFFFFFFF
