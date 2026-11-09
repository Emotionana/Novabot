"""多账号网关管理。

一个账号一个 OneBotClient，一个 Pipeline，一份独立状态。
一个挂了不影响别的。

启动时按配置建，运行中能增删。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass

from ..access_service import AccessService
from ..models import ChatType
from .bridge import parse_event, split_message, to_outbound
from .onebot import OneBotClient, OneBotConfig, OneBotError
from .progress import ProgressTurn

log = logging.getLogger(__name__)


@dataclass
class AccountRuntime:
    """一个账号的运行态。"""

    id: str = ""
    bot_qq: str = ""
    client: OneBotClient | None = None
    pipeline: object | None = None
    enabled: bool = True
    # 统计
    received: int = 0
    sent: int = 0
    errors: int = 0
    last_message_at: float = 0.0
    last_error: str = ""
    _task: asyncio.Task | None = None

    def status(self) -> dict:
        base = self.client.status() if self.client else {"connected": False}
        return {
            "id": self.id,
            "bot_qq": self.bot_qq,
            "enabled": self.enabled,
            "connected": base.get("connected", False),
            "received": self.received,
            "sent": self.sent,
            "errors": self.errors,
            "last_message_at": self.last_message_at,
            "last_error": self.last_error or base.get("last_error", ""),
            "failures": base.get("failures", 0),
            "dropped": getattr(self.client, "dropped", 0) if self.client else 0,
        }


class GatewayManager:
    """管所有账号的连接。

    每个账号：
      收事件 → 解析 → pipeline.handle() → 发送
    """

    def __init__(self, settings, memory=None, persona=None, *, llm_factory=None):
        self.settings = settings
        self.memory = memory
        self.persona = persona
        self.llm_factory = llm_factory
        self.proactive = None  # 主动私聊服务，main 里挂上来
        self.accounts: dict[str, AccountRuntime] = {}
        self._pending: dict[tuple[str, str], dict] = {}
        self._active_private: set[tuple[str, str]] = set()
        self.access = AccessService(self)
        self._running = False
        # 总开关真的开着、并且确实尝试起账号了才置 True。
        # 光"这个对象存在"不算接入已开——网页状态栏会照着这个显示。
        self.active = False

    # ==================================================================
    # 生命周期
    # ==================================================================

    async def start_all(self) -> None:
        """按配置起所有账号。"""
        self._running = True

        # 账号列表和开关都来自配置文件（一个账号一段配置，塞不进 key-value 参数表）。
        # 开关优先看用户在界面上显式改过的值；没改过就用 TOML 里的。
        from .. import config

        cfg = config.get()
        try:
            if self.settings.is_custom("gateway.enabled"):
                enabled = bool(self.settings.get("gateway.enabled"))
            else:
                enabled = bool(cfg.gateway.enabled)
        except (KeyError, AttributeError):
            enabled = bool(cfg.gateway.enabled)
        if not enabled:
            log.info("接入总开关关着，不起任何连接")
            self.active = False
            return

        self.active = True
        accounts = cfg.gateway.accounts or {}

        if not accounts:
            log.info("没有配置任何账号")
            return

        for acc_id, acc_cfg in accounts.items():
            if not isinstance(acc_cfg, dict):
                continue
            try:
                await self.add_account(acc_id, acc_cfg)
            except Exception as e:
                log.error("账号 %s 启动失败：%s", acc_id, e)

        log.info("共启动 %d 个账号", len(self.accounts))

    async def stop_all(self) -> None:
        """优雅退出：先停收，再等处理完，最后断连并释放模型连接。"""
        self._running = False

        # 1. 停所有客户端（不再收新消息；内部会等在途消息处理完）
        for rt in self.accounts.values():
            if rt.client:
                try:
                    await rt.client.stop()
                except Exception as e:
                    log.warning("停 %s 出错：%s", rt.id, e)

        # 2. 释放语音引擎和模型连接
        for rt in self.accounts.values():
            llm = getattr(rt.pipeline, "llm", None) if rt.pipeline else None
            closer = getattr(llm, "close", None)
            if not callable(closer):
                continue
            try:
                result = closer()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                log.warning("关模型连接出错：%s", e)

        for rt in self.accounts.values():
            voice = getattr(rt.pipeline, "voice", None) if rt.pipeline else None
            if voice is not None:
                try:
                    await voice.close()
                except Exception as e:  # noqa: BLE001
                    log.warning("关语音引擎出错：%s", e)

        log.info("所有连接已关闭")

    # ==================================================================
    # 账号管理
    # ==================================================================

    async def add_account(self, acc_id: str, cfg: dict) -> AccountRuntime:
        """加一个账号并连上。"""
        if acc_id in self.accounts:
            log.warning("账号 %s 已存在", acc_id)
            return self.accounts[acc_id]

        ob_cfg = OneBotConfig(
            bot_qq=str(cfg.get("bot_qq") or ""),
            # 兼容配置文件里的两种叫法：url / onebot_url，token / onebot_token
            url=str(cfg.get("url") or cfg.get("onebot_url") or ""),
            listen=str(cfg.get("listen") or ""),
            token=str(cfg.get("token") or cfg.get("onebot_token") or ""),
            impl=str(cfg.get("impl") or "snowluma"),
            reverse=bool(cfg.get("reverse") or False),
            reconnect_sec=int(cfg.get("reconnect_sec") or 5),
            reconnect_max=int(cfg.get("reconnect_max") or 60),
            max_failures=int(cfg.get("max_failures") or 20),
        )

        rt = AccountRuntime(id=acc_id, bot_qq=ob_cfg.bot_qq)
        client = OneBotClient(ob_cfg, on_event=lambda ev: self._on_event(acc_id, ev))
        rt.client = client
        client.send_guard = self._outbound_guard

        # 每个账号一个 Pipeline
        rt.pipeline = self._make_pipeline(acc_id, client)
        self._sync_bot_qq(rt, ob_cfg.bot_qq)

        self.accounts[acc_id] = rt
        await client.start()
        log.info("账号 %s 已启动（%s @ %s）", acc_id, ob_cfg.impl, ob_cfg.url or ob_cfg.listen)
        return rt

    async def remove_account(self, acc_id: str) -> None:
        rt = self.accounts.pop(acc_id, None)
        if rt and rt.client:
            await rt.client.stop()
            log.info("账号 %s 已移除", acc_id)

    def get(self, acc_id: str) -> AccountRuntime | None:
        return self.accounts.get(acc_id)

    def list_status(self) -> list[dict]:
        return [rt.status() for rt in self.accounts.values()]

    async def retry(self, acc_id: str) -> bool:
        """手动重试一个账号（失败次数太多停了之后用）。"""
        rt = self.accounts.get(acc_id)
        if rt is None or rt.client is None:
            return False
        rt.client.reset_failures()
        await rt.client.stop()
        await rt.client.start()
        log.info("账号 %s 手动重连", acc_id)
        return True

    # ==================================================================
    # 消息流
    # ==================================================================

    async def _on_event(self, acc_id: str, event: dict) -> None:
        """收到一条事件。"""
        rt = self.accounts.get(acc_id)
        if rt is None or not rt.enabled:
            return

        # 撤回不参与会话锁，直接取消对应回复
        if event.get("post_type") == "notice" and event.get("notice_type") in ("group_recall", "friend_recall"):
            await self._handle_recall(acc_id, event)
            return

        if event.get("post_type") in ("request", "notice"):
            try:
                if await self.access.handle_event(acc_id, event):
                    return
            except Exception as e:  # noqa: BLE001
                log.warning("访问事件处理失败：%s", e)

        progress = None
        pending_key = None
        try:
            msg = parse_event(event, impl=rt.client.cfg.impl if rt.client else "snowluma")
            if msg is None:
                return

            msg.account_id = acc_id
            try:
                if await self.access.handle_message(acc_id, rt, msg):
                    return
            except Exception as e:  # noqa: BLE001
                log.warning("访问确认处理失败：%s", e)

            if (
                self.proactive is not None
                and msg.chat_type is ChatType.PRIVATE
                and msg.sender_qq
                and msg.sender_qq != "self"
                and msg.text
            ):
                try:
                    self.proactive.on_user_message(
                        acc_id, msg.sender_qq, msg.text, at=msg.event_at
                    )
                except Exception as e:  # noqa: BLE001
                    log.debug("主动回复反馈失败：%s", e)
            rt.received += 1
            rt.last_message_at = time.time()

            if rt.pipeline is None:
                return
            await self._ensure_llm(rt)

            # 私聊长任务执行中：后续消息直接 steer 进当前 DSH 回合，不排队。
            private_key = (acc_id, msg.chat_id)
            if (
                msg.chat_type is ChatType.PRIVATE
                and private_key in self._active_private
                and (msg.text or "").strip()
            ):
                try:
                    if await rt.pipeline.steer(msg):
                        log.info("私聊消息已直接注入 DSH 会话：%s / %s", acc_id, msg.chat_id)
                        return
                except Exception as e:  # noqa: BLE001
                    log.warning("私聊 steer 失败：%s", e)

            if msg.chat_type is ChatType.PRIVATE:
                self._active_private.add(private_key)
            if msg.message_id:
                progress = ProgressTurn(
                    client=rt.client,
                    chat_id=msg.chat_id,
                    account_id=acc_id,
                    message_id=msg.message_id,
                    settings=rt.pipeline.settings,
                    allow=self._progress_allowed(rt, msg),
                    fallback=self._progress_fallback(rt),
                )
                await progress.start()
                pending_key = (acc_id, msg.message_id)

            task = asyncio.create_task(rt.pipeline.handle(msg, progress=progress))
            if pending_key is not None:
                self._pending[pending_key] = {"task": task, "progress": progress}
            try:
                result = await task
            except asyncio.CancelledError:
                if progress is not None:
                    await progress.cancel()
                return
            finally:
                if pending_key is not None:
                    self._pending.pop(pending_key, None)
                self._active_private.discard(private_key)

            if progress is not None and progress.canceled:
                return
            if result.error:
                rt.errors += 1
                rt.last_error = result.error
                log.warning("处理失败（%s）：%s", acc_id, result.error)
                if progress is not None:
                    await progress.finish()
                return

            if not result.replied:
                if progress is not None:
                    await progress.finish()
                return

            if progress is not None:
                await progress.finish()
                if progress.canceled:
                    return
            await self._send_result(rt, result, msg.chat_id, progress=progress)

        except asyncio.CancelledError:
            if progress is not None:
                await progress.cancel()
            return
        except Exception as e:
            rt.errors += 1
            rt.last_error = f"{type(e).__name__}: {e}"[:200]
            if progress is not None:
                await progress.finish()
            log.exception("账号 %s 处理事件出错", acc_id)

    def _progress_allowed(self, rt: AccountRuntime, msg) -> bool:
        try:
            if not bool(rt.pipeline.settings.get("reply.thinking_progress")):
                return False
        except Exception:  # noqa: BLE001
            return False
        if msg.chat_type is ChatType.PRIVATE:
            return True
        try:
            if not bool(rt.pipeline.settings.get("reply.thinking_group")):
                return False
            if msg.poke:
                return True
            bot_qq = str(rt.bot_qq or "")
            if bot_qq and bot_qq in (msg.at_list or []):
                return True
            words = rt.pipeline.settings.get("reply.wake_words") or []
            text = msg.text or ""
            return any(str(w) and str(w) in text for w in words)
        except Exception:  # noqa: BLE001
            return False

    def _progress_fallback(self, rt: AccountRuntime) -> bool:
        """DSH 有真实工具/中间说明时，不用固定“我理一下”文案。"""
        try:
            settings = rt.pipeline.settings
            if bool(settings.get("llm.agent_enabled")) and str(settings.get("llm.agent_type") or "") == "dsh":
                return False
        except Exception:  # noqa: BLE001
            pass
        return True

    async def _handle_recall(self, acc_id: str, event: dict) -> None:
        rt = self.accounts.get(acc_id)
        if rt is not None and not bool(rt.pipeline.settings.get("reply.recall_cancels", True)):
            return
        mid = str(event.get("message_id") or "")
        if not mid:
            return
        entry = self._pending.pop((acc_id, mid), None)
        if entry is None:
            return
        progress = entry.get("progress")
        if progress is not None:
            await progress.cancel()
            if progress.episode_id:
                try:
                    self.settings.db.execute(
                        "UPDATE episodes SET gone = 1, gone_reason = 'recalled', gone_at = ? "
                        "WHERE id = ? AND account_id = ?",
                        [time.time(), progress.episode_id, acc_id],
                    )
                except Exception as e:  # noqa: BLE001
                    log.debug("标记撤回消息失败：%s", e)
        task = entry.get("task")
        if task is not None and not task.done():
            task.cancel()
        log.info("用户撤回消息，已取消思考：%s/%s", acc_id, mid)

    def _outbound_allowed(self) -> bool:
        try:
            if not bool(self.settings.get("runtime.master_enabled")):
                return False
            if bool(self.settings.get("runtime.dry_run")):
                return False
        except Exception:  # noqa: BLE001
            return True
        return True

    def _outbound_guard(self, outbound) -> bool:
        try:
            master = bool(self.settings.get("runtime.master_enabled"))
            dry = bool(self.settings.get("runtime.dry_run"))
        except Exception:  # noqa: BLE001
            return True
        if master and not dry:
            return True
        text = str(getattr(outbound, "text", "") or "")[:80]
        kind = "总开关关闭" if not master else "全局试运行"
        log.info("[%s] 拦截出站：%s %s", kind, getattr(outbound, "chat_id", ""), text)
        return False

    def _record_sent_id(self, rt, result, sent) -> None:
        """把 bot 实际发出的 QQ message_id 写回 episode，供之后引用查找。"""
        ep_id = getattr(result, "bot_episode_id", None)
        if not ep_id or not isinstance(sent, dict):
            return
        mid = sent.get("message_id") or sent.get("id")
        if not mid:
            return
        try:
            self.settings.db.execute(
                "UPDATE episodes SET raw = json_set(COALESCE(raw, '{}'), '$.message_id', ?) "
                "WHERE id = ? AND account_id = ?",
                [str(mid), int(ep_id), rt.id],
            )
        except Exception as e:  # noqa: BLE001
            log.debug("记录 bot message_id 失败：%s", e)

    async def _send_result(self, rt: AccountRuntime, result, chat_id: str, *, progress=None) -> None:
        """把 pipeline 的结果发出去。"""
        if rt.client is None:
            return
        if progress is not None and progress.canceled:
            return
        if not self._outbound_allowed():
            self._outbound_guard(type("X", (), {"chat_id": chat_id, "text": getattr(result, "text", "")})())
            return

        # 优先发 result.messages（已经拆好的）
        outs = getattr(result, "messages", None) or []
        if not outs:
            text = getattr(result, "text", "") or ""
            if text:
                outs = [to_outbound(chat_id, p) for p in split_message(text)]

        for index, ob in enumerate(outs):
            if progress is not None and progress.canceled:
                return
            if index:
                try:
                    settings = getattr(getattr(rt, "pipeline", None), "settings", None)
                    lo = float((settings.get("output.part_delay_min") if settings else None) or 0.25)
                    hi = float((settings.get("output.part_delay_max") if settings else None) or 0.7)
                    await asyncio.sleep(max(0.0, random.uniform(min(lo, hi), max(lo, hi))))
                except Exception:  # noqa: BLE001
                    await asyncio.sleep(0.35)
            try:
                text = getattr(ob, "text", "") or ""
                voice = getattr(ob, "voice", None)

                # 语音单独发（QQ 语音消息不带正文）；失败就把正文补出去
                if voice:
                    bare = to_outbound(chat_id, "")
                    bare.voice = voice
                    bare.voice_only = True
                    bare.voice_fallback_text = getattr(ob, "voice_fallback_text", "") or ""
                    bare.at = getattr(ob, "at", None) or []
                    try:
                        sent = await rt.client.send(bare)
                        if index == 0:
                            self._record_sent_id(rt, result, sent)
                        rt.sent += 1
                        continue
                    except OneBotError as e:
                        rt.errors += 1
                        rt.last_error = str(e)[:200]
                        log.warning("语音发送失败（%s），补发文字：%s", rt.id, e)
                        if text:
                            sent = await rt.client.send(to_outbound(chat_id, text))
                            if index == 0:
                                self._record_sent_id(rt, result, sent)
                            rt.sent += 1
                            continue
                        raise

                # 太长的再拆一次
                if len(text) > 500:
                    parts = split_message(text, max_chars=500)
                    for part_index, part in enumerate(parts):
                        sent = await rt.client.send(to_outbound(chat_id, part))
                        if index == 0 and part_index == 0:
                            self._record_sent_id(rt, result, sent)
                        rt.sent += 1
                        await asyncio.sleep(0.3)   # 别发太快
                else:
                    sent = await rt.client.send(ob)
                    if index == 0:
                        self._record_sent_id(rt, result, sent)
                    rt.sent += 1
            except OneBotError as e:
                rt.errors += 1
                rt.last_error = str(e)[:200]
                log.warning("发送失败（%s）：%s", rt.id, e)
                break   # 这号发不出去，后面的也别试了
            except Exception as e:
                rt.errors += 1
                rt.last_error = f"{type(e).__name__}: {e}"[:200]
                log.exception("发送出错")

    # ==================================================================
    # 内部
    # ==================================================================

    async def _ensure_llm(self, rt: AccountRuntime) -> None:
        """设置里后来开了 Agent / DSH 换了端口时，把 pipeline 的模型补上。"""
        p = rt.pipeline
        if p is None or self.llm_factory is None:
            return
        from ..adapters.llm.agent import AgentAdapter
        from ..settings import Settings

        current = getattr(p, "llm", None)
        db = getattr(self.settings, "db", None)
        if db is None:
            return
        try:
            st = Settings(db, account_id=rt.id)
            agent_on = bool(st.get("llm.agent_enabled"))
            endpoint = str(st.get("llm.agent_endpoint") or "").strip()
        except Exception:  # noqa: BLE001
            agent_on, endpoint = False, ""
        stale = False
        if isinstance(current, AgentAdapter):
            stale = (not agent_on) or (endpoint and endpoint != current.endpoint)
        elif agent_on and endpoint:
            # 之前是空/API 模型，现在切到 Agent
            stale = True
        elif current is None:
            stale = bool(agent_on and endpoint)
        if not stale:
            return
        try:
            new = self.llm_factory(rt.id)
        except Exception as e:  # noqa: BLE001
            log.warning("账号 %s 补模型失败：%s", rt.id, e)
            return
        if new is None:
            closer = getattr(current, "close", None)
            if callable(closer):
                try:
                    result = closer()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:  # noqa: BLE001
                    pass
            p.llm = None
            return
        closer = getattr(current, "close", None)
        if callable(closer):
            try:
                result = closer()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001
                pass
        p.llm = new
        log.info("账号 %s 的模型已更新：%s @ %s", rt.id, type(new).__name__, getattr(new, "endpoint", ""))

    def _make_pipeline(self, acc_id: str, client: OneBotClient):
        """给账号建一个 Pipeline。"""
        from ..core.pipeline import Pipeline
        from ..core.tokens import get_meter
        from ..settings import Settings

        # 每个账号一套 settings（按 account_id 隔离）。
        # 用网关自己那份 settings 绑定的库，别去摸全局单例——测试和嵌入场景才不会串库。
        db = getattr(self.settings, "db", None)
        if db is None:
            from ..db import get_db

            db = get_db()
        acc_settings = Settings(db, account_id=acc_id)

        llm = None
        if self.llm_factory:
            try:
                llm = self.llm_factory(acc_id)
            except Exception as e:
                log.warning("账号 %s 的模型初始化失败：%s", acc_id, e)
        if llm is None:
            log.warning(
                "账号 %s 没有可用模型，收到消息会记下来但不会回复"
                "（检查 config/local.toml 的 [llm.xxx] 和参数 llm.default）",
                acc_id,
            )

        voice = None
        try:
            from .. import config as _config
            from ..voice import VoiceService

            cfg = _config.get()
            # 引擎配了就给账号挂上语音服务；开不开由参数 voice.enabled 决定
            if cfg.voice.enabled or cfg.voice.engines:
                voice = VoiceService(db, self.persona, cfg, acc_settings)
        except Exception as e:  # noqa: BLE001
            log.warning("账号 %s 语音初始化失败：%s", acc_id, e)

        return Pipeline(
            settings=acc_settings,
            memory=self.memory,
            persona=self.persona,
            llm=llm,
            meter=get_meter(),
            voice=voice,
            gateway=self,
        )

    # ==================================================================
    # 主动私聊用的入口
    # ==================================================================

    def llm_for(self, acc_id: str):
        """取这个账号的模型；没有就临时建一个。"""
        rt = self.accounts.get(acc_id)
        llm = getattr(getattr(rt, "pipeline", None), "llm", None)
        if llm is not None:
            return llm
        if self.llm_factory is not None:
            try:
                return self.llm_factory(acc_id)
            except Exception as e:  # noqa: BLE001
                log.warning("建模型失败（%s）：%s", acc_id, e)
        return None

    async def send_file_to_chat(self, acc_id: str, chat_id: str, path: str, name: str = "") -> bool:
        if not self._outbound_allowed():
            self._outbound_guard(type("X", (), {"chat_id": chat_id, "text": name or path})())
            return True
        rt = self.accounts.get(acc_id)
        if rt is None or rt.client is None:
            return False
        from ..models import OutboundMessage

        await rt.client.send(OutboundMessage(chat_id=chat_id, file=path, file_name=name))
        return True

    async def send_to_chat(self, acc_id: str, chat_id: str, text: str) -> bool:
        """按 chat_id 发：private:xxx / group:xxx。提醒服务用这个。"""
        if not self._outbound_allowed():
            self._outbound_guard(type("X", (), {"chat_id": chat_id, "text": text})())
            return True
        if chat_id.startswith("private:"):
            return await self.send_private(acc_id, chat_id[8:], text)
        rt = self.accounts.get(acc_id)
        if rt is None or not rt.enabled:
            return False
        if rt.client is None or not getattr(rt.client, "connected", False):
            return False
        try:
            await rt.client.send(to_outbound(chat_id, text))
            rt.sent += 1
            return True
        except Exception as e:  # noqa: BLE001
            rt.errors += 1
            rt.last_error = f"发送失败：{e}"[:200]
            log.warning("发送到 %s 失败：%s", chat_id, e)
            return False

    def online_accounts(self) -> list[str]:
        return [
            acc_id
            for acc_id, rt in self.accounts.items()
            if rt.enabled and rt.client is not None and getattr(rt.client, "connected", False)
        ]

    async def send_private(self, acc_id: str, qq: str, text: str) -> bool:
        """主动发一条私聊。失败返回 False，不抛。"""
        if not self._outbound_allowed():
            self._outbound_guard(type("X", (), {"chat_id": f"private:{qq}", "text": text})())
            return True
        rt = self.accounts.get(acc_id)
        if rt is None or not rt.enabled:
            return False
        if rt.client is None or not getattr(rt.client, "connected", False):
            return False
        try:
            await rt.client.send(to_outbound(f"private:{qq}", text))
            rt.sent += 1
            return True
        except Exception as e:  # noqa: BLE001
            rt.errors += 1
            rt.last_error = f"主动发送失败：{e}"[:200]
            log.warning("主动私聊发送失败（%s → %s）：%s", acc_id, qq, e)
            return False

    def _sync_bot_qq(self, rt: AccountRuntime, bot_qq: str) -> None:
        """把账号配置里的 bot_qq 写进参数表。

        注意筛判断"@ 的是不是我"用的是参数 gateway.bot_qq，
        而 QQ 号只写在账号配置里——不同步的话，@ 永远不算叫我。

        账号作用域那份给运行时用；全局那份给界面看（没设过才写，
        多账号时不会被后一个账号覆盖掉前一个的显示）。
        """
        settings = getattr(rt.pipeline, "settings", None)
        if settings is None or not bot_qq:
            return
        try:
            # 注意用 is_custom 而不是 get：get 会回落到全局值，
            # 第二个账号就会误以为"已经有 bot_qq 了"，结果沿用第一个号的 QQ。
            if not settings.is_custom("gateway.bot_qq"):
                settings.set("gateway.bot_qq", bot_qq)
                log.info("账号 %s 的 bot_qq 同步到参数表：%s", rt.id, bot_qq)
        except Exception as e:
            log.warning("同步 bot_qq 失败（%s）：%s", rt.id, e)
            return

        # 全局那份只补空，别覆盖
        try:
            from ..settings import Settings

            db = getattr(self.settings, "db", None)
            if db is not None:
                global_settings = Settings(db)
                if not global_settings.is_custom("gateway.bot_qq"):
                    global_settings.set("gateway.bot_qq", bot_qq)
        except Exception as e:  # noqa: BLE001
            log.debug("同步全局 bot_qq 失败：%s", e)

    # ==================================================================
    # 状态
    # ==================================================================

    def summary(self) -> dict:
        total_recv = sum(rt.received for rt in self.accounts.values())
        total_sent = sum(rt.sent for rt in self.accounts.values())
        online = sum(1 for rt in self.accounts.values() if rt.client and rt.client.connected)
        return {
            "accounts": len(self.accounts),
            "dropped": sum(rt.status().get("dropped", 0) for rt in self.accounts.values()),
            "online": online,
            "received": total_recv,
            "sent": total_sent,
            "errors": sum(rt.errors for rt in self.accounts.values()),
        }
