"""模型适配层。

两类接法：
  接法 A（llm）    —— Novabot 当主控，适配器只负责调一次模型
  接法 B（platform）—— Novabot 当被控，给外部 agent 平台提供记忆和人格

目前只做接法 A。
"""

from __future__ import annotations

from .llm.base import Capabilities, Message, ModelAdapter, Reply

__all__ = ["ModelAdapter", "Message", "Reply", "Capabilities"]
