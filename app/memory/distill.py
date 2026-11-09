"""蒸馏：把一批原话压成事实和摘要。

设计的四条约束：
  1. 不在写路径上——收消息只存原文，蒸馏是后台的事
  2. 一次一批（默认 20 条），能少调一次就少调一次
  3. 太短的消息、bot 自己说的话，不送模型，直接标已处理
  4. 失败的批次记次数，超过 3 次不再重试——别让一条坏数据无限烧钱

产出去哪：
  facts  → 新皮层（cortex.apply_distill，含冲突处理）
  gist   → gists 表
  relations 现在不用（联想图还没接线）
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass

from ..models import Episode
from .cortex import Cortex
from .hippocampus import Hippocampus

log = logging.getLogger(__name__)

DISTILL_SYSTEM = """你是记忆整理器。读一段聊天记录，抽出值得长期记住的东西。

只输出 JSON，不要解释。格式：
{
  "facts": [
    {"subject": "说话人的QQ号", "subject_type": "person", "predicate": "住在",
     "object": "杭州", "text": "住在杭州", "tier": 3, "source_idx": 0,
     "confidence": 0.8}
  ],
  "relations": [{"subj": "QQ号", "pred": "认识", "obj": "QQ号"}],
  "gist": "一句话概括这段对话"
}

规则：
- subject 必须填说话人的 QQ 号（记录里 QQ: 后面那串数字）
- 只抽对方关于自己的稳定信息：名字/称呼、住哪、做什么、喜欢什么、和谁什么关系、约定
- 八卦、玩笑、一次性的情绪、bot 自己的话，都不抽
- tier：0 身份级（名字/称呼）；3 重要（工作/住址/家人/约定）；2 一般；1 琐碎
- 拿不准就不抽，facts 可以是空数组
- source_idx 是这条事实出自第几行记录（从 0 开始）"""

DISTILL_TMPL = "记录：\n{lines}"


@dataclass
class DistillStats:
    """一轮蒸馏的结果。"""

    batches: int = 0           # 调了几次模型
    episodes: int = 0          # 处理了几条原话
    facts: int = 0
    duplicates: int = 0
    superseded: int = 0
    conflicts: int = 0
    gists: int = 0
    skipped: int = 0           # 太短/全是自己的话，没送模型
    failed: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def __bool__(self) -> bool:
        return any(asdict(self).values())


class Distiller:
    """后台蒸馏器。一个进程一个。"""

    def __init__(
        self,
        memory,
        llm,
        settings,
        *,
        hippocampus: Hippocampus | None = None,
        cortex: Cortex | None = None,
    ):
        self.memory = memory
        self.llm = llm
        self.settings = settings
        self.hippocampus = hippocampus or memory.hippocampus
        self.cortex = cortex or memory.cortex

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    async def run_once(self) -> DistillStats:
        """捞一批该蒸的，蒸掉。没东西就立刻返回。"""
        stats = DistillStats()
        batch_size = int(self.settings.get("memory.distill_batch") or 20)
        min_age = int(self.settings.get("memory.distill_min_age_sec") or 60)
        min_chars = int(self.settings.get("memory.distill_min_chars") or 10)

        episodes = self.hippocampus.distill_candidates(
            limit=batch_size, min_age_sec=min_age
        )
        if not episodes:
            return stats

        # 按会话分组：一次模型调用只处理一个会话，时间线才连贯
        groups: dict[tuple[str, str], list[Episode]] = {}
        for ep in episodes:
            groups.setdefault((ep.account_id, ep.chat_id), []).append(ep)

        for (account_id, chat_id), rows in groups.items():
            worth = [
                e
                for e in rows
                if e.sender_qq != "self" and len((e.text or "").strip()) >= min_chars
            ]
            if not worth:
                self.hippocampus.mark_distilled([e.id for e in rows])
                stats.skipped += len(rows)
                continue
            stats.batches += 1
            stats.episodes += len(rows)
            await self._distill_batch(account_id, chat_id, worth, rows, stats)

        return stats

    async def run_forever(self, interval: float | None = None) -> None:
        """定时跑。被 cancel 时安静退出。"""
        if interval is None:
            interval = float(self.settings.get("memory.distill_interval_sec") or 180)
        log.info("蒸馏循环启动，每 %.0f 秒一轮", interval)
        while True:
            try:
                await asyncio.sleep(interval)
                stats = await self.run_once()
                if stats:
                    log.info("蒸馏一轮：%s", stats.to_dict())
            except asyncio.CancelledError:
                log.info("蒸馏循环退出")
                raise
            except Exception:
                log.exception("蒸馏出错，下一轮再试")
                await asyncio.sleep(5)

    # ------------------------------------------------------------------
    # 一批
    # ------------------------------------------------------------------

    async def _distill_batch(
        self,
        account_id: str,
        chat_id: str,
        worth: list[Episode],
        rows: list[Episode],
        stats: DistillStats,
    ) -> None:
        prompt = DISTILL_TMPL.format(lines=self._format(worth))
        try:
            reply = await self.llm.chat(
                system=DISTILL_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                account_id=account_id,
                stateless=True,
                max_tokens=800,
                temperature=0,
            )
        except Exception as e:
            log.warning("蒸馏调用失败（%s）：%s", chat_id, e)
            self.hippocampus.mark_distill_failed([e.id for e in rows])
            stats.failed += 1
            return

        if not getattr(reply, "ok", False):
            log.warning("蒸馏调用返回错误（%s）：%s", chat_id, getattr(reply, "error", ""))
            self.hippocampus.mark_distill_failed([e.id for e in rows])
            stats.failed += 1
            return

        data = _extract_json(getattr(reply, "text", "") or "")
        if not isinstance(data, dict):
            log.warning("蒸馏返回的不是 JSON（%s）：%r", chat_id, (getattr(reply, "text", "") or "")[:120])
            self.hippocampus.mark_distill_failed([e.id for e in rows])
            stats.failed += 1
            return

        try:
            out = self.cortex.apply_distill(account_id, chat_id, worth, data)
        except Exception as e:
            log.warning("蒸馏落库失败（%s）：%s", chat_id, e)
            self.hippocampus.mark_distill_failed([e.id for e in rows])
            stats.failed += 1
            return

        # 成功（哪怕模型什么都没抽出来）就标已处理，别反复烧钱
        self.hippocampus.mark_distilled([e.id for e in rows])
        stats.facts += out.get("facts", 0)
        stats.duplicates += out.get("duplicate", 0)
        stats.superseded += out.get("superseded", 0)
        stats.conflicts += out.get("conflict", 0)
        stats.gists += out.get("gists", 0)

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    def _format(self, eps: list[Episode]) -> str:
        lines = []
        for i, e in enumerate(eps):
            name = e.sender_nick or e.sender_qq
            when = time.strftime("%m-%d %H:%M", time.localtime(e.event_at or time.time()))
            text = (e.text or "").strip()[:200]
            lines.append(f"{i}. [{when}] {name}(QQ:{e.sender_qq})：{text}")
        return "\n".join(lines)


def _extract_json(text: str):
    """从模型输出里抠出 JSON。抠不出来返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:])
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        return None
    try:
        # raw_decode：只解析第一个 JSON 值，后面的解释性文字直接忽略
        value, _ = json.JSONDecoder().raw_decode(text[min(starts):])
        return value
    except json.JSONDecodeError:
        return None
