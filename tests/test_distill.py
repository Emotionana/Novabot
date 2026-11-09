"""蒸馏：原话 → 事实。

这是"记忆系统"从能存到能用的那一步：没有它，facts 永远是空表，
模型只能看到最近几句原话，问"我住哪"只能靠上下文碰运气。
"""

from __future__ import annotations

import json
import time

import pytest

from app.memory.distill import Distiller, _extract_json
from tests.helpers import ScriptedLLM, group_msg

FACT_JSON = json.dumps(
    {
        "facts": [
            {
                "subject": "111",
                "subject_type": "person",
                "predicate": "住在",
                "object": "杭州",
                "text": "住在杭州",
                "tier": 3,
                "source_idx": 0,
                "confidence": 0.9,
            }
        ],
        "relations": [],
        "gist": "说了自己住哪、做什么",
    },
    ensure_ascii=False,
)


def add_message(mem, text: str, *, sender: str = "111", age: int = 600, chat_id: str = "group:1"):
    msg = group_msg(text, sender=sender, chat_id=chat_id)
    msg.event_at = time.time() - age
    return mem.remember(msg)


@pytest.fixture()
def distiller(mem, settings):
    llm = ScriptedLLM(FACT_JSON)
    return Distiller(mem, llm, settings), llm


@pytest.mark.asyncio
async def test_batch_creates_facts_and_gist(mem, db, distiller):
    d, llm = distiller
    ids = [
        add_message(mem, "我叫小林，在杭州上班"),
        add_message(mem, "最近在学吉他，练了两周"),
        add_message(mem, "周末一般在家待着"),
    ]

    stats = await d.run_once()

    assert stats.batches == 1
    assert stats.facts == 1
    assert stats.gists == 1
    assert len(llm.calls) == 1

    fact = db.query_one("SELECT * FROM facts WHERE subject_id = ?", ["111"])
    assert fact is not None
    assert fact["object"] == "杭州"
    assert fact["account_id"] == "default"

    assert db.scalar("SELECT COUNT(*) FROM gists", default=0) == 1
    assert db.scalar("SELECT COUNT(*) FROM episodes WHERE distilled = 0", default=0) == 0

    # 再跑一轮：没有新的了，不该再调模型
    stats2 = await d.run_once()
    assert stats2.batches == 0
    assert len(llm.calls) == 1

    # 检索时能看到这条事实
    context = mem.build_context(
        chat_id="group:1", sender_qq="111", account_id="default", text="我住哪"
    )
    assert "住在杭州" in context


@pytest.mark.asyncio
async def test_short_messages_skipped_without_model(mem, db, distiller):
    d, llm = distiller
    add_message(mem, "嗯")
    add_message(mem, "好")

    stats = await d.run_once()

    assert stats.skipped == 2
    assert llm.calls == [], "太短的消息不该送模型"
    assert db.scalar("SELECT COUNT(*) FROM episodes WHERE distilled = 0", default=0) == 0


@pytest.mark.asyncio
async def test_bot_messages_are_not_distilled(mem, distiller):
    d, llm = distiller
    add_message(mem, "我叫小林，在杭州上班")
    add_message(mem, "我是机器人说的话，不该被抽成事实", sender="self")

    await d.run_once()

    prompt = llm.calls[0]["messages"][0]["content"]
    assert "QQ:111" in prompt
    assert "QQ:self" not in prompt


@pytest.mark.asyncio
async def test_bad_json_retries_then_gives_up(mem, db, settings):
    llm = ScriptedLLM("抱歉，我不太确定")
    d = Distiller(mem, llm, settings)
    add_message(mem, "我叫小林，在杭州上班")

    stats = await d.run_once()
    assert stats.failed == 1
    row = db.query_one("SELECT distilled, distill_attempts FROM episodes")
    assert row["distilled"] == 0
    assert row["distill_attempts"] == 1

    for _ in range(2):
        await d.run_once()

    row = db.query_one("SELECT distilled, distill_attempts FROM episodes")
    assert row["distill_attempts"] == 3

    # 三次都失败后不再捞它
    before = len(llm.calls)
    stats = await d.run_once()
    assert stats.batches == 0
    assert len(llm.calls) == before


@pytest.mark.asyncio
async def test_model_error_counts_as_failure(mem, db, settings):
    llm = ScriptedLLM(error=RuntimeError("网络炸了"))
    d = Distiller(mem, llm, settings)
    add_message(mem, "我叫小林，在杭州上班")

    stats = await d.run_once()

    assert stats.failed == 1
    assert db.scalar("SELECT distill_attempts FROM episodes", default=0) == 1


@pytest.mark.asyncio
async def test_code_fence_is_parsed(mem, db, settings):
    llm = ScriptedLLM("```json\n" + FACT_JSON + "\n```")
    d = Distiller(mem, llm, settings)
    add_message(mem, "我叫小林，在杭州上班")

    stats = await d.run_once()

    assert stats.facts == 1
    assert db.scalar("SELECT COUNT(*) FROM facts", default=0) == 1


def test_extract_json_tolerates_noise():
    assert _extract_json('好的：{"a": 1} 以上') == {"a": 1}
    assert _extract_json("没有 JSON") is None
    assert _extract_json("") is None
