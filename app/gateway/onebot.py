"""OneBot v11 接入。

只认协议，不认实现。SnowLuma / NapCat / Lagrange / LLOneBot 都能接。

两种连接方向：
  正向 WS（reverse=True）   我们开服务，它连过来
  反向 WS（reverse=False）  我们是客户端，去连它

发消息这块是核心：收到 → 处理 → 发出去，缺一环就断。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import time
from dataclasses import dataclass, replace

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 不同实现的字段差异
# ---------------------------------------------------------------------------

IMPL_PROFILES = {
    "snowluma": {"card_path": "sender.card", "array_format": True},
    "napcat": {"card_path": "sender.card", "array_format": True},
    "lagrange": {"card_path": "sender.card", "array_format": True},
    "llonebot": {"card_path": "card", "array_format": True},
    "go-cqhttp": {"card_path": "sender.card", "array_format": False},
    "custom": {"card_path": "sender.card", "array_format": True},
}


def profile_of(name: str) -> dict:
    return IMPL_PROFILES.get(name, IMPL_PROFILES["custom"])


# ---------------------------------------------------------------------------
# 连接
# ---------------------------------------------------------------------------


@dataclass
class OneBotConfig:
    bot_qq: str = ""
    url: str = ""              # 反向模式：去连这个
    listen: str = ""           # 正向模式：在这听
    token: str = ""
    impl: str = "snowluma"
    reverse: bool = False      # True = 我们开服务
    reconnect_sec: int = 5
    reconnect_max: int = 60    # 退避上限
    heartbeat_sec: int = 30
    max_failures: int = 20     # 连续失败这么多次就停


class OneBotError(Exception):
    pass


class OneBotClient:
    """一个账号一个客户端。

    只管连接和收发，不碰业务逻辑。
    """

    # 单连接最多同时挂着多少条事件。超了丢新的——被刷屏时优先保住进程。
    MAX_PENDING_EVENTS = 200

    def __init__(self, cfg: OneBotConfig, *, on_event=None):
        self.cfg = cfg
        self.on_event = on_event

        self.ws = None
        self.connected = False
        self.self_id = cfg.bot_qq
        self.failures = 0
        self.last_error = ""
        self.last_connected_at = 0.0
        self._pending: dict[str, asyncio.Future] = {}
        self.send_guard = None  # 可选：返回 False 时只记录，不真正发出
        self._echo_seq = 0
        self.dropped = 0
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        # 事件处理不能堵在读循环里（发消息要等 echo，而 echo 只能由读循环读到）。
        # 所以事件派生成任务跑；同一会话仍串行，避免回复顺序错乱。
        self._dispatch_tasks: set[asyncio.Task] = set()
        self._chat_locks: dict[str, asyncio.Lock] = {}
        self._inflight = asyncio.Semaphore(32)
        self.profile = profile_of(cfg.impl)

    # ==================================================================
    # 连接管理
    # ==================================================================

    async def start(self) -> None:
        """起后台连接任务。"""
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_forever(), name=f"onebot-{self.cfg.bot_qq}")

    async def stop(self) -> None:
        self._stop.set()
        if self.ws is not None:
            with contextlib.suppress(Exception):
                await self.ws.close()
            self.ws = None
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        # 等派生的消息处理跑完（最多 5 秒），别把在处理的消息丢掉
        await self.wait_idle()
        self.connected = False

    async def _run_forever(self) -> None:
        """带退避的重连。"""
        delay = self.cfg.reconnect_sec
        while not self._stop.is_set():
            try:
                await self._connect_once()
                # 连上后重置
                delay = self.cfg.reconnect_sec
                self.failures = 0
                await self._read_loop()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.failures += 1
                self.last_error = f"{type(e).__name__}: {e}"[:200]
                self.connected = False
                log.warning(
                    "OneBot %s 断开（第 %d 次）：%s",
                    self.cfg.bot_qq, self.failures, self.last_error,
                )

                if self.failures >= self.cfg.max_failures:
                    log.error(
                        "OneBot %s 连续失败 %d 次，停止重连。需要手动重试。",
                        self.cfg.bot_qq, self.failures,
                    )
                    break

            if self._stop.is_set():
                break

            # 退避
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.cfg.reconnect_max)

    async def _connect_once(self) -> None:
        import websockets

        if self.cfg.reverse:
            raise OneBotError("正向 WS 需要由 web 层提供 handler，这里不支持直接起")

        if not self.cfg.url:
            raise OneBotError("没配 url")

        headers = {}
        if self.cfg.token:
            headers["Authorization"] = f"Bearer {self.cfg.token}"

        log.info("连 OneBot：%s", self.cfg.url)
        self.ws = await websockets.connect(
            self.cfg.url,
            additional_headers=headers,
            ping_interval=20,
            ping_timeout=20,
            max_size=16 * 1024 * 1024,   # 图片和转发可能很大
        )
        self.connected = True
        self.last_connected_at = time.time()
        log.info("OneBot %s 已连接", self.cfg.bot_qq)

    async def _read_loop(self) -> None:
        """读消息，分两路：API 响应 / 事件通知。

        事件不在这里 await 处理结果——发消息要等 echo，而 echo 只能由这个循环读到。
        在循环里 await 业务逻辑会自锁（旧实现就是这样，每条回复卡 45 秒）。
        """
        if self.ws is None:
            return

        async for raw in self.ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                log.debug("收到非 JSON：%s", str(raw)[:100])
                continue

            echo = data.get("echo")
            if echo and echo in self._pending:
                fut = self._pending.pop(echo)
                if not fut.done():
                    fut.set_result(data)
                continue

            if data.get("post_type") and self.on_event:
                if len(self._dispatch_tasks) >= self.MAX_PENDING_EVENTS:
                    self.dropped += 1
                    log.warning(
                        "积压 %d 条事件，丢弃一条（累计 %d，%s）",
                        len(self._dispatch_tasks),
                        self.dropped,
                        str(data.get("raw_message") or data.get("message"))[:40],
                    )
                    continue
                task = asyncio.create_task(self._dispatch(data))
                self._dispatch_tasks.add(task)
                task.add_done_callback(self._dispatch_tasks.discard)

    async def _dispatch(self, event: dict) -> None:
        """派发一条事件：同一会话串行，不同会话并发。"""
        # 撤回必须绕过会话锁，否则正被等待的回复不会取消
        if event.get("post_type") == "notice" and event.get("notice_type") in ("group_recall", "friend_recall"):
            try:
                await self.on_event(event)
            except Exception:  # noqa: BLE001
                log.exception("撤回事件处理出错")
            return
        key = str(event.get("group_id") or event.get("user_id") or "")
        # 私聊不占会话锁：任务执行中的后续消息要立刻送到 DSH 会话，
        # 由 GatewayManager 走 steer 快路径；群聊仍按群串行。
        if event.get("group_id"):
            lock = self._chat_locks.setdefault(key, asyncio.Lock())
            async with self._inflight, lock:
                try:
                    await self.on_event(event)
                except Exception:
                    log.exception("事件处理出错")
        else:
            async with self._inflight:
                try:
                    await self.on_event(event)
                except Exception:
                    log.exception("事件处理出错")

    async def wait_idle(self, timeout: float = 5.0) -> None:
        """等所有在途事件处理完。超时就取消，别卡住退出。"""
        tasks = [t for t in self._dispatch_tasks if not t.done()]
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), timeout=timeout
                )
            except TimeoutError:
                log.warning("还有 %d 条消息没处理完，强制取消", len(tasks))
                for t in tasks:
                    t.cancel()
        # 锁表按会话数增长，清掉已经没人用的
        if len(self._chat_locks) > 512:
            self._chat_locks = {k: v for k, v in self._chat_locks.items() if v.locked()}

    # ==================================================================
    # 调 API
    # ==================================================================

    async def call(self, action: str, params: dict | None = None, *, timeout: float = 15.0) -> dict:
        """调 OneBot API，返回响应体。"""
        if self.ws is None or not self.connected:
            raise OneBotError("没连上")

        self._echo_seq += 1
        echo = f"{self.cfg.bot_qq}-{self._echo_seq}-{random.randint(0, 9999)}"
        payload = {"action": action, "params": params or {}, "echo": echo}

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[echo] = fut

        try:
            await self.ws.send(json.dumps(payload, ensure_ascii=False))
            data = await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError:
            self._pending.pop(echo, None)
            raise OneBotError(f"{action} 超时（{timeout}s）") from None
        except Exception:
            self._pending.pop(echo, None)
            raise

        retcode = data.get("retcode", 0)
        if retcode not in (0, 1):   # 1 是异步已受理
            raise OneBotError(f"{action} 失败：retcode={retcode} {data.get('message') or data.get('wording') or ''}")

        return data

    # ==================================================================
    # 发消息（核心）
    # ==================================================================

    async def delete_msg(self, message_id: str) -> dict:
        """撤回自己发出的消息；不支持时由调用方忽略。"""
        mid = int(message_id) if str(message_id).isdigit() else message_id
        return await self.call("delete_msg", {"message_id": mid})

    async def send_private_msg(self, user_id: str, segments: list, *, retry: int = 2) -> dict:
        return await self._send_with_retry(
            "send_private_msg",
            {"user_id": int(user_id) if str(user_id).isdigit() else user_id, "message": segments},
            retry=retry,
        )

    async def send_group_msg(self, group_id: str, segments: list, *, retry: int = 2) -> dict:
        return await self._send_with_retry(
            "send_group_msg",
            {"group_id": int(group_id) if str(group_id).isdigit() else group_id, "message": segments},
            retry=retry,
        )

    async def _send_with_retry(self, action: str, params: dict, *, retry: int) -> dict:
        """发送失败重试。

        有些失败是永久的（被拉黑、群不存在），重试没用，直接抛。
        """
        last = ""
        for i in range(retry + 1):
            try:
                return await self.call(action, params)
            except OneBotError as e:
                last = str(e)
                msg = last.lower()
                # 这些重试也没用
                if any(x in msg for x in ("群不存在", "not found", "已解散", "被拉黑", "禁言")):
                    raise
                if i < retry:
                    wait = 1.5 * (i + 1)
                    log.debug("%s 失败（%s），%.1fs 后重试", action, last[:60], wait)
                    await asyncio.sleep(wait)
        raise OneBotError(last)

    async def send(self, outbound) -> dict | None:
        """把 OutboundMessage 发出去。

        这是接入层对外的唯一发送入口。
        """
        guard = self.send_guard
        if guard is not None:
            try:
                if not guard(outbound):
                    return {"blocked": True}
            except Exception:  # noqa: BLE001
                return {"blocked": True}
        # 拼消息段
        segments: list[dict] = []

        for at in getattr(outbound, "at", []) or []:
            qq = str(at)
            segments.append({"type": "at", "data": {"qq": "all" if qq == "all" else qq}})

        text = getattr(outbound, "text", "") or ""
        voice_only = bool(getattr(outbound, "voice_only", False))
        if text and not voice_only:
            segments.append({"type": "text", "data": {"text": text}})

        image = getattr(outbound, "image", None)
        if image:
            segments.append({"type": "image", "data": {"file": image}})

        sticker = getattr(outbound, "sticker", None)
        sticker_emoji_id = str(getattr(outbound, "sticker_emoji_id", "") or "")
        if sticker and sticker_emoji_id and not text and not getattr(outbound, "at", None) and not getattr(outbound, "voice", None):
            chat_id = getattr(outbound, "chat_id", "") or ""
            params = {"emoji_id": sticker_emoji_id}
            if chat_id.startswith("group:"):
                params["group_id"] = int(chat_id[6:]) if chat_id[6:].isdigit() else chat_id[6:]
            elif chat_id.startswith("private:"):
                params["user_id"] = int(chat_id[8:]) if chat_id[8:].isdigit() else chat_id[8:]
            if "group_id" in params or "user_id" in params:
                try:
                    return await self.call("send_custom_face", params)
                except OneBotError:
                    pass  # 协议端不支持时回退成普通图片
        if sticker:
            # 本地表情优先走 image + sub_type=1；协议端不支持时仍按图片发
            segments.append({"type": "image", "data": {"file": str(sticker), "sub_type": 1}})

        voice = getattr(outbound, "voice", None)
        if voice:
            # 语音一般传 base64 或本地路径，不同实现不一样
            import base64

            if isinstance(voice, bytes):
                segments.append(
                    {"type": "record", "data": {"file": "base64://" + base64.b64encode(voice).decode()}}
                )
            else:
                segments.append({"type": "record", "data": {"file": str(voice)}})

        file_path = getattr(outbound, "file", None)
        if file_path:
            from pathlib import Path

            name = getattr(outbound, "file_name", "") or Path(str(file_path)).name
            chat_id = getattr(outbound, "chat_id", "") or ""
            if chat_id.startswith("group:"):
                return await self.call("upload_group_file", {
                    "group_id": int(chat_id[6:]) if chat_id[6:].isdigit() else chat_id[6:],
                    "file": str(file_path), "name": name,
                })
            if chat_id.startswith("private:"):
                return await self.call("upload_private_file", {
                    "user_id": int(chat_id[8:]) if chat_id[8:].isdigit() else chat_id[8:],
                    "file": str(file_path), "name": name,
                })
            return None

        if not segments:
            return None

        chat_id = getattr(outbound, "chat_id", "") or ""
        try:
            if chat_id.startswith("group:"):
                return await self.send_group_msg(chat_id[6:], segments)
            if chat_id.startswith("private:"):
                return await self.send_private_msg(chat_id[8:], segments)
        except OneBotError:
            fallback = str(getattr(outbound, "voice_fallback_text", "") or "")
            if getattr(outbound, "voice", None) and fallback:
                restored = replace(
                    outbound, text=fallback, voice=None,
                    voice_only=False, voice_fallback_text="",
                )
                return await self.send(restored)
            raise

        raise OneBotError(f"看不懂的 chat_id：{chat_id}")

    # ==================================================================
    # 常用查询
    # ==================================================================

    async def get_login_info(self) -> dict:
        r = await self.call("get_login_info")
        return r.get("data") or {}

    async def get_group_info(self, group_id: str) -> dict:
        r = await self.call("get_group_info", {"group_id": int(group_id)})
        return r.get("data") or {}

    async def get_group_member_info(self, group_id: str, user_id: str) -> dict:
        r = await self.call(
            "get_group_member_info",
            {"group_id": int(group_id), "user_id": int(user_id), "no_cache": False},
        )
        return r.get("data") or {}

    async def get_stranger_info(self, user_id: str) -> dict:
        r = await self.call("get_stranger_info", {"user_id": int(user_id), "no_cache": False})
        return r.get("data") or {}

    # ==================================================================
    # 状态
    # ==================================================================

    def status(self) -> dict:
        return {
            "bot_qq": self.cfg.bot_qq,
            "connected": self.connected,
            "impl": self.cfg.impl,
            "url": self.cfg.url,
            "failures": self.failures,
            "last_error": self.last_error,
            "last_connected_at": self.last_connected_at,
        }

    def reset_failures(self) -> None:
        """手动重试时调。"""
        self.failures = 0
        self.last_error = ""
