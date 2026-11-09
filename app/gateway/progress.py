"""长思考进度：延迟提示、工具进度、撤回清理。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time

from ..models import OutboundMessage

log = logging.getLogger(__name__)

_REGISTRY: dict[str, ProgressTurn] = {}


def get_progress(token: str) -> ProgressTurn | None:
    return _REGISTRY.get(token)


def _message_id(result) -> str:
    if not isinstance(result, dict):
        return ""
    data = result.get("data")
    if isinstance(data, dict) and data.get("message_id") is not None:
        return str(data["message_id"])
    for key in ("message_id", "messageId"):
        if result.get(key) is not None:
            return str(result[key])
    return ""


class ProgressTurn:
    """一次回复的进度消息。

    B：固定两条延迟提示。
    C：DSH 工具事件通过 HTTP 回调触发 emit_tool。
    """

    def __init__(
        self,
        *,
        client,
        chat_id: str,
        account_id: str,
        message_id: str,
        settings,
        allow: bool = True,
        fallback: bool = True,
        turn_id: str = "",
    ):
        self.client = client
        self.chat_id = chat_id
        self.account_id = account_id
        self.message_id = message_id
        self.settings = settings
        self.allow = bool(allow)
        self.fallback = bool(fallback)
        self.turn_id = turn_id or secrets.token_urlsafe(10)
        self.token = secrets.token_urlsafe(18)
        self.finished = False
        self.canceled = False
        self.thinking_progress = bool(self.settings.get("reply.thinking_progress", False))
        self.thinking_tools = bool(self.settings.get("reply.thinking_tools", False))
        self.thinking_max = int(self.settings.get("reply.thinking_max", 3) or 3)
        self.progress_endpoint = str(self.settings.get("reply.progress_endpoint", "") or "").strip()
        self.message_ids: list[str] = []
        self._lock = asyncio.Lock()
        self._timers: list[asyncio.Task] = []
        self._last_tool_text = ""
        self.episode_id: int | None = None
        self.real_progress = False
        _REGISTRY[self.token] = self

    def _get(self, key, default=None):
        try:
            return self.settings.get(key)
        except Exception:  # noqa: BLE001
            return default

    def _enabled(self) -> bool:
        return self.thinking_progress

    def _max_messages(self) -> int:
        try:
            return max(1, min(5, self.thinking_max))
        except Exception:  # noqa: BLE001
            return 3

    def _endpoint(self) -> str:
        custom = self.progress_endpoint
        if custom:
            return custom
        try:
            port = int(self._get("server.port", 19100) or 19100)
        except Exception:  # noqa: BLE001
            port = 19100
        return f"http://127.0.0.1:{port}/api/internal/progress"

    def payload(self) -> dict:
        return {
            "url": self._endpoint(),
            "token": self.token,
            "turn_id": self.turn_id,
        }

    async def start(self) -> None:
        # 不发固定“我理一下/还在整理”文案；只等 DSH 自己产生的中间说明。
        return

    async def send(self, text: str, *, kind: str = "timer") -> bool:
        if not self.allow or not self._enabled() or self.finished or self.canceled:
            return False
        if self.client is None:
            return False
        async with self._lock:
            if self.finished or self.canceled:
                return False
            if len(self.message_ids) >= self._max_messages():
                return False
            try:
                result = await self.client.send(OutboundMessage(chat_id=self.chat_id, text=text))
            except Exception as e:  # noqa: BLE001
                log.debug("进度消息发送失败：%s", e)
                return False
            mid = _message_id(result)
            if mid:
                self.message_ids.append(mid)
            log.info("发送思考进度（%s）：%s", kind, text)
            return True

    async def emit_tool(self, event_type: str, tool: str = "") -> bool:
        # 工具名不是思考过程，不再生成“我先跑一下命令”这种固定文案。
        return False

    async def emit_commentary(self, text: str) -> bool:
        """只转发 DSH 自己产生的中间说明，不生成固定人机文案。"""
        if not self.allow or not self._enabled() or not self.thinking_tools:
            return False
        if self.finished or self.canceled:
            return False
        clean = " ".join(str(text or "").split()).strip()
        if not clean:
            return False
        if clean == self._last_tool_text:
            return False
        self._last_tool_text = clean
        sent = await self.send(clean[:240], kind="assistant")
        if sent:
            self.real_progress = True
        return sent

    async def finish(self) -> None:
        self.finished = True
        for task in self._timers:
            task.cancel()
        _REGISTRY.pop(self.token, None)

    async def cancel(self, *, delete: bool = True) -> None:
        if self.canceled:
            return
        self.canceled = True
        self.finished = True
        for task in self._timers:
            task.cancel()
        _REGISTRY.pop(self.token, None)
        if delete and self.client is not None:
            for mid in list(self.message_ids):
                try:
                    await self.client.delete_msg(mid)
                except Exception as e:  # noqa: BLE001
                    log.debug("撤回进度消息失败：%s", e)
            self.message_ids.clear()

    def status(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "finished": self.finished,
            "canceled": self.canceled,
            "messages": len(self.message_ids),
            "created_at": time.time(),
        }


def close_all() -> None:
    for turn in list(_REGISTRY.values()):
        with contextlib.suppress(Exception):
            turn.finished = True
    _REGISTRY.clear()
