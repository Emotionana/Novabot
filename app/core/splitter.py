"""把模型回复拆成像手机聊天那样的一条条消息。

不是固定回几句：按句子、分句、换行和显式 ||| 拆；短句合并，长句才在
逗号/分号处断开发送。URL、代码块、@ 和数字不拆。
"""

from __future__ import annotations

import re

STRONG = "。！？!?…~～"
WEAK = "，、；：,;:"
CLOSERS = "”’」』）)]】》"


def _clean(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text or "")
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    return text.strip()


def _protect(text: str) -> bool:
    return "```" in text or "http://" in text or "https://" in text


def _split_long_piece(piece: str, target: int) -> list[str]:
    """一句太长时按逗号/分号断，仍太长才硬断；不碰 URL。"""
    soft = max(12, int(target * 0.55))
    if len(piece) <= soft:
        return [piece]
    if _protect(piece):
        return [piece]
    chunks: list[str] = []
    buf = ""
    for ch in piece:
        buf += ch
        if (ch in WEAK and len(buf) >= soft) or len(buf) >= target:
            chunks.append(buf.strip())
            buf = ""
    if buf.strip():
        chunks.append(buf.strip())
    return [c for c in chunks if c]


def split_natural(
    text: str,
    max_chars: int = 38,
    max_parts: int = 4,
    *,
    min_chars: int = 4,
    merge_short: bool = True,
) -> list[str]:
    """返回自然拆分后的消息列表；只有一个语义单元时返回一条。"""
    text = _clean(text)
    if not text:
        return []
    max_chars = max(8, int(max_chars or 38))
    max_parts = max(1, int(max_parts or 4))

    # 作者/人格卡显式写的 ||| 优先，代表“这里想断一条”
    if "|||" in text:
        pieces = [p.strip() for p in re.split(r"\s*\|\|\|\s*", text) if p.strip()]
        return pieces[:max_parts]

    # 代码/链接整段发，避免拆坏
    if _protect(text):
        return [text][:max_parts]

    # 先按强标点/换行分句，保留标点
    sentences = re.split(r"(?<=[。！？!?…~～])\s*|\n+", text)
    sentences = [s.strip() for s in sentences if s.strip()]

    # 一句长句再按逗号/分号拆；短句保持
    units: list[str] = []
    for s in sentences:
        units.extend(_split_long_piece(s, max_chars))

    if not units:
        return []

    # 短句并到相邻句，避免“嗯。”“哦。”每条都单独发
    merged: list[str] = []
    for u in units:
        if merged and merge_short and (
            len(u) <= 3 or (len(merged[-1]) <= 2 and len(merged[-1]) + len(u) <= max_chars)
        ):
            merged[-1] = (merged[-1] + u).strip()
        else:
            merged.append(u)

    # 仍然超过上限：从最长的相邻对开始合并，而不是固定回几条
    while len(merged) > max_parts:
        idx = min(range(len(merged) - 1), key=lambda i: len(merged[i]) + len(merged[i + 1]))
        merged[idx] = (merged[idx] + merged[idx + 1]).strip()
        merged.pop(idx + 1)

    # 去掉纯标点/空条；合并后重新清理
    out: list[str] = []
    for p in merged:
        p = p.strip()
        if not p or not re.search(r"[\w\u4e00-\u9fff]", p):
            continue
        out.append(p)
    return out[:max_parts] or [text]
