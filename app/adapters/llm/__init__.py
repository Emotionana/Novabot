"""大语言模型适配器。"""

from .base import BaseAdapter, Capabilities, Message, ModelAdapter, Reply, ToolCall
from .openai_compat import OpenAICompatAdapter, create_adapter

__all__ = [
    "ModelAdapter", "BaseAdapter", "Message", "Reply", "ToolCall", "Capabilities",
    "OpenAICompatAdapter", "create_adapter",
]
