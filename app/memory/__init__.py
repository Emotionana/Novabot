"""记忆系统。

按大脑分区：
  hippocampus  海马 —— 情景记忆
  amygdala     杏仁核 —— 情绪
  cortex       新皮层 —— 语义（事实 / 摘要 / 人物）
  graph        联想图
  selfmodel    自我参照
  recall       检索调度

对外统一通过 MemorySystem 调用。
"""

from __future__ import annotations

from .system import MemorySystem, get_memory

__all__ = ["MemorySystem", "get_memory"]
