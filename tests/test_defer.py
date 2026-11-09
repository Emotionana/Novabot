"""DEFER：规则判不出时才花一次极小调用。"""

from __future__ import annotations

import pytest

from app.core.pipeline import Pipeline
from app.core.tokens import TokenMeter
from tests.helpers import ScriptedLLM, group_msg


def make_pipe(settings, mem, llm, meter=None):
    settings.set("reply.group_mode", "smart")
    settings.set("reply.cooldown_sec", 0)
    settings.set("reply.max_per_min", 0)
    return Pipeline(settings=settings, memory=mem, llm=llm, meter=meter or TokenMeter())


@pytest.mark.asyncio
async def test_high_score_replies(settings, mem):
    llm = ScriptedLLM("85", "好的")
    pipe = make_pipe(settings, mem, llm)

    result = await pipe.handle(group_msg())

    assert result.replied is True
    assert result.text == "好的"
    labels = [r.label for r in pipe.meter.recent(10)]
    assert labels == ["prescreen", "reply"]


@pytest.mark.asyncio
async def test_low_score_ignored(settings, mem):
    llm = ScriptedLLM("10")
    pipe = make_pipe(settings, mem, llm)

    result = await pipe.handle(group_msg())

    assert result.replied is False
    assert len(llm.calls) == 1, "只该有判定那一次调用"


@pytest.mark.asyncio
async def test_switch_off_never_calls_model(settings, mem):
    settings.set("reply.defer_judge", False)
    llm = ScriptedLLM("99")
    pipe = make_pipe(settings, mem, llm)

    result = await pipe.handle(group_msg())

    assert result.replied is False
    assert llm.calls == []


@pytest.mark.asyncio
async def test_model_error_means_no_reply(settings, mem):
    llm = ScriptedLLM(error=RuntimeError("炸了"))
    pipe = make_pipe(settings, mem, llm)

    result = await pipe.handle(group_msg())

    assert result.replied is False


@pytest.mark.asyncio
async def test_garbage_answer_means_no_reply(settings, mem):
    llm = ScriptedLLM("我不知道")
    pipe = make_pipe(settings, mem, llm)

    result = await pipe.handle(group_msg())

    assert result.replied is False


@pytest.mark.asyncio
async def test_no_llm_means_no_reply(settings, mem):
    pipe = make_pipe(settings, mem, None)

    result = await pipe.handle(group_msg())

    assert result.replied is False
