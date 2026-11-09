"""外部 agent 平台（DSH / TRAE / CodeBuddy / 自定义 HTTP）的 LLM 适配器。

协议（POST 到用户填的 endpoint）：
    {"protocol":1,"session_id":"private:1","account_id":"","system":"...",
     "messages":[{"role":"user","content":"..."}],"max_tokens":300}
响应：
    {"reply":"...","session_id":"dsh-xxx","usage":{...}}
也兼容 text/content/message/choices[0].message.content。

会话映射复用 platform_sessions：返回的 external session 绑定到当前 chat_id。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import httpx

from .base import BaseAdapter, Capabilities, Reply

log = logging.getLogger(__name__)

_TYPES = {"dsh", "trae", "codebuddy", "custom", "agent"}


class AgentAdapter(BaseAdapter):
    def __init__(
        self,
        *,
        platform: str,
        endpoint: str,
        token: str = "",
        timeout: float = 180,
        max_retries: int = 1,
        db=None,
        settings=None,
        name: str = "agent",
        client: httpx.AsyncClient | None = None,
    ):
        self.name = name
        self.platform = platform if platform in _TYPES else "custom"
        self.endpoint = endpoint.strip()
        self.token = token.strip()
        self.timeout = float(timeout or 180)
        self.max_retries = max(0, int(max_retries))
        self.db = db
        self.settings = settings
        self._client: httpx.AsyncClient | None = client
        self._session_epochs: dict[str, int] = {}

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
            h["X-Platform-Token"] = self.token
        return h

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                headers=self._headers(),
            )
        return self._client

    async def steer(self, text: str, *, session_id: str, account_id: str) -> bool:
        """把私聊后续消息直接注入正在跑的 DSH 回合，不排队等上一轮。"""
        payload = {
            "steer": True,
            "session_id": str(session_id or ""),
            "account_id": str(account_id or ""),
            "messages": [{"role": "user", "content": str(text or "")}],
            "stateless": False,
        }
        try:
            r = await self.client.post(self.endpoint, json=payload, timeout=5.0)
            if r.status_code != 200:
                return False
            data = r.json()
            return bool(data.get("accepted"))
        except Exception as e:  # noqa: BLE001
            log.debug("DSH steer 失败：%s", e)
            return False

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def capabilities(self) -> Capabilities:
        return Capabilities(tools=False, streaming=False, max_context=32768)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    async def chat(
        self,
        system: str = "",
        messages=None,
        *,
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        session_id: str = "",
        account_id: str = "",
        **kwargs,
    ) -> Reply:
        if not self.endpoint:
            return Reply(error="没有配置 agent endpoint")
        normalized = self._normalize(system, messages)
        session_key = str(session_id or "")
        bot_name = ""
        if self.db is not None and account_id:
            try:
                row = self.db.query_one("SELECT name FROM accounts WHERE id = ?", [account_id])
                bot_name = str(row["name"] or "") if row else ""
            except Exception:  # noqa: BLE001
                bot_name = ""
        payload = {
            "protocol": 1,
            "platform": self.platform,
            "stateless": bool(kwargs.get("stateless")),
            "session_id": self._session_key(session_key),
            "account_id": str(account_id or ""),
            "bot_name": bot_name,
            "system": system or "",
            "messages": [m for m in normalized if m.get("role") != "system"],
            "permissions": kwargs.get("permissions") or {},
        }
        progress = kwargs.get("progress")
        if progress is not None:
            with contextlib.suppress(Exception):
                payload["progress"] = progress.payload()
        if max_tokens:
            payload["max_tokens"] = int(max_tokens)
        if temperature is not None:
            payload["temperature"] = float(temperature)
        if self.settings is not None and self.platform == "dsh":
            try:
                model = str(self.settings.get("llm.dsh_model") or "").strip()
                effort = str(self.settings.get("llm.dsh_reasoning_effort") or "low").strip()
                if model:
                    payload["model"] = {"model": model, "reasoningEffort": effort or "low"}
            except Exception:  # noqa: BLE001
                pass

        if self.settings is not None:
            with contextlib.suppress(Exception):
                self.settings.get("llm.session_compact_turns")
            with contextlib.suppress(Exception):
                self.settings.get("llm.session_rotate_turns")
        if self.settings is not None:
            with contextlib.suppress(Exception):
                self.settings.get("llm.private_session_compact_turns")
            with contextlib.suppress(Exception):
                self.settings.get("llm.private_session_rotate_turns")
            with contextlib.suppress(Exception):
                self.settings.get("llm.group_session_compact_turns")
            with contextlib.suppress(Exception):
                self.settings.get("llm.group_session_rotate_turns")
        is_private = session_key.startswith("private:")
        scope = "private" if is_private else "group"
        policy = {}
        for field in ("compact_turns", "rotate_turns"):
            key = f"llm.{scope}_session_{field}"
            try:
                value = int(self.settings.get(key) or 0) if self.settings is not None else 0
            except Exception:  # noqa: BLE001
                value = 0
            if value > 0:
                policy[field] = value
        if policy:
            payload["session_policy"] = policy
        try:
            timeout_sec = int(self.settings.get("llm.agent_timeout_sec") or 600) if self.settings is not None else 600
        except Exception:  # noqa: BLE001
            timeout_sec = 600
        payload["agent_timeout_sec"] = max(10, min(3600, timeout_sec))
        try:
            mode = str(self.settings.get("reply.long_task_mode") or "manual") if self.settings is not None else "manual"
        except Exception:  # noqa: BLE001
            mode = "manual"
        try:
            max_rounds = int(self.settings.get("reply.long_task_max_rounds") or 3) if self.settings is not None else 3
        except Exception:  # noqa: BLE001
            max_rounds = 3
        mode = mode if mode in ("off", "manual", "auto") else "manual"
        payload["long_task_mode"] = mode
        payload["long_task_max_rounds"] = max(1, min(10, max_rounds))

        if mode == "auto":
            request_timeout = max(float(self.timeout), float(timeout_sec) * int(payload["long_task_max_rounds"]) + 60)
        elif mode == "off":
            request_timeout = max(float(self.timeout), 3600.0)
        else:
            # 插件会在 timeout_sec 时 cancel 并返回进度；HTTP 必须比它晚一点，
            # 否则客户端先超时，只能拿到本地固定文案。
            request_timeout = max(float(self.timeout), float(timeout_sec) + 60.0)

        last = ""
        for attempt in range(self.max_retries + 1):
            try:
                r = await self.client.post(self.endpoint, json=payload, timeout=request_timeout)
                if r.status_code != 200:
                    last = f"HTTP {r.status_code}: {r.text[:200]}"
                    if self._is_session_error(last) and self._session_epochs.get(session_key, 0) < 3:
                        self._session_epochs[session_key] = self._session_epochs.get(session_key, 0) + 1
                        payload["session_id"] = self._session_key(session_key)
                        log.warning("DSH 会话不可用，切换到新会话 key=%s epoch=%s",
                                    session_key, self._session_epochs[session_key])
                        continue
                    if r.status_code in (400, 401, 403, 404):
                        return Reply(error=last)
                    continue
                data = r.json()
                return self._parse(data, session_id, account_id)
            except httpx.TimeoutException:
                return Reply(text="DSH 回合超时了，但这次没拿到可读进度。你可以让我继续下一段。")
            except Exception as e:  # noqa: BLE001
                last = str(e)[:200]
            if attempt < self.max_retries:
                await asyncio.sleep(0.8 * (attempt + 1))
        return Reply(error=last or "agent 调用失败")

    def _session_key(self, session_id: str) -> str:
        epoch = int(self._session_epochs.get(session_id, 0) or 0)
        return f"{session_id}#nb{epoch}" if epoch else session_id

    @staticmethod
    def _is_session_error(text: str) -> bool:
        low = str(text or "").casefold()
        needles = ("session", "会话", "archiv", "归档", "not found", "不存在",
                   "closed", "已关闭", "disposed", "invalid session", "无效")
        return any(x in low for x in needles)

    def _parse(self, data: dict, session_id: str, account_id: str) -> Reply:
        text = ""
        for key in ("reply", "text", "content", "message"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                text = value
                break
        if not text:
            try:
                text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            except Exception:  # noqa: BLE001
                text = ""
        external = str(data.get("session_id") or data.get("sessionId") or "").strip()
        if external and self.db is not None:
            try:
                from ..platform.dsh import PlatformProvider

                provider = PlatformProvider(self.db, None, None)
                provider.bind_session(
                    self.platform, external,
                    chat_id=session_id or f"{self.platform}:{external}",
                    account_id=account_id,
                )
            except Exception as e:  # noqa: BLE001
                log.debug("绑定平台会话失败：%s", e)
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        if not text:
            return Reply(error=f"agent 返回里没有文本：{str(data)[:200]}", raw=data)
        return Reply(text=text.strip(), usage=usage, raw=data)
