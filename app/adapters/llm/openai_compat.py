"""OpenAI 兼容适配器。

DeepSeek、Qwen、Moonshot、本地 Ollama、vLLM 全都走这个。

对话接口：POST {base_url}/chat/completions
向量接口：POST {base_url}/embeddings（可选）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .base import BaseAdapter, Capabilities, Reply, ToolCall

log = logging.getLogger(__name__)


class OpenAICompatAdapter(BaseAdapter):
    """OpenAI 兼容的模型适配器。"""

    def __init__(
        self,
        *,
        name: str = "openai_compat",
        base_url: str,
        api_key: str = "",
        model: str,
        embed_model: str = "",
        timeout: float = 120,
        max_retries: int = 2,
        extra_headers: dict[str, str] | None = None,
    ):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.embed_model = embed_model
        self.timeout = timeout
        self.max_retries = max_retries
        self.extra_headers = extra_headers or {}
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        h.update(self.extra_headers)
        return h

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                headers=self._headers(),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # 对话
    # ------------------------------------------------------------------

    async def chat(
        self,
        system: str = "",
        messages=None,
        *,
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        **kwargs,
    ) -> Reply:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._normalize(system, messages),
        }
        if max_tokens:
            payload["max_tokens"] = int(max_tokens)
        if temperature is not None:
            payload["temperature"] = float(temperature)
        if tools:
            payload["tools"] = tools

        last_err = ""
        for attempt in range(self.max_retries + 1):
            try:
                r = await self.client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                )
                if r.status_code != 200:
                    last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                    # 400 这类错误重试也没用
                    if r.status_code in (400, 401, 403, 404):
                        return Reply(error=last_err)
                    continue

                data = r.json()
                return self._parse(data)

            except httpx.TimeoutException:
                last_err = f"超时（{self.timeout}s）"
            except Exception as e:
                last_err = str(e)[:200]

            if attempt < self.max_retries:
                wait = 1.0 * (attempt + 1)
                log.debug("第 %d 次失败（%s），%.0fs 后重试", attempt + 1, last_err, wait)
                await asyncio.sleep(wait)

        return Reply(error=last_err or "未知错误")

    def _parse(self, data: dict) -> Reply:
        """解析响应。"""
        try:
            choices = data.get("choices") or []
            if not choices:
                return Reply(error=f"没有 choices：{str(data)[:200]}", raw=data)

            msg = choices[0].get("message") or {}
            text = msg.get("content") or ""

            # 工具调用
            calls: list[ToolCall] = []
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                args = fn.get("arguments") or "{}"
                try:
                    import json

                    parsed = json.loads(args) if isinstance(args, str) else args
                except Exception:
                    parsed = {"_raw": args}
                calls.append(
                    ToolCall(
                        id=tc.get("id", ""),
                        name=fn.get("name", ""),
                        arguments=parsed if isinstance(parsed, dict) else {"_raw": parsed},
                    )
                )

            return Reply(
                text=text,
                tool_calls=calls,
                usage=data.get("usage") or {},
                raw=data,
            )
        except Exception as e:
            return Reply(error=f"解析失败：{e}", raw=data)

    # ------------------------------------------------------------------
    # 向量
    # ------------------------------------------------------------------

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.embed_model:
            raise NotImplementedError("没有配置向量模型")
        if not texts:
            return []

        r = await self.client.post(
            f"{self.base_url}/embeddings",
            json={"model": self.embed_model, "input": texts},
        )
        if r.status_code != 200:
            raise RuntimeError(f"向量化失败：HTTP {r.status_code} {r.text[:200]}")

        data = r.json()
        items = data.get("data") or []
        items.sort(key=lambda x: x.get("index", 0))
        return [it["embedding"] for it in items]

    # ------------------------------------------------------------------
    # 能力
    # ------------------------------------------------------------------

    def capabilities(self) -> Capabilities:
        return Capabilities(
            tools=True,
            images=False,
            streaming=False,
            max_context=8192,
            supports_temperature=True,
        )

    def __repr__(self) -> str:
        return f"<{self.name} model={self.model} url={self.base_url}>"


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


def create_adapter(name: str, cfg: dict) -> OpenAICompatAdapter:
    """按配置建适配器。"""
    return OpenAICompatAdapter(
        name=name,
        base_url=cfg.get("base_url") or "",
        api_key=cfg.get("api_key") or "",
        model=cfg.get("model") or "",
        embed_model=cfg.get("embed_model") or "",
        timeout=float(cfg.get("timeout") or 120),
        max_retries=int(cfg.get("max_retries") or 2),
        extra_headers=cfg.get("headers") or None,
    )
