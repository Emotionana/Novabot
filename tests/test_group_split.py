"""群聊单独的字数上限 / 拆分条数上限；私聊不受影响。

需求：群聊设置里加「消息字数上限」（默认 50 字）和「拆分条数上限」（默认最多 3 条），
私聊继续走输出面板里的 output.split_chars / output.split_max。
"""

from __future__ import annotations

from app.core.pipeline import Pipeline
from app.settings import all_params

# 四句各 12~14 字：短句不会被并掉，42 字上限下正好拆成 4 条
FOUR_SENTENCES = (
    "今天下午去了一趟超市买水果。"
    "回来路上顺便把快递取了。"
    "晚上煮了面还煎了个鸡蛋。"
    "吃完把厨房收拾干净才坐下。"
)


class _Ctx:
    """只提供 _format_out 用到的属性，不启动真正的 pipeline。"""

    output = None

    def __init__(self, **settings):
        self.settings = {"output.split_enabled": True, **settings}


class _Msg:
    def __init__(self, group: bool):
        self.chat_id = "group:100" if group else "private:100"
        self.is_group = group


def _parts(ctx: _Ctx, text: str, group: bool) -> list[str]:
    return [m.text for m in Pipeline._format_out(ctx, _Msg(group), text)]


def test_group_defaults_registered():
    params = all_params()
    assert params["output.group_split_chars"].default == 50
    assert params["output.group_split_max"].default == 3


def test_private_keeps_old_limits():
    """私聊没有配置群聊参数时，仍然按 42 字 / 4 条走。"""
    assert len(_parts(_Ctx(), FOUR_SENTENCES, group=False)) == 4


def test_group_defaults_to_three_parts():
    """群聊没配置时用默认值：最多 3 条。"""
    assert len(_parts(_Ctx(), FOUR_SENTENCES, group=True)) == 3


def test_group_limits_are_configurable():
    ctx = _Ctx(**{"output.group_split_max": 2})
    assert len(_parts(ctx, FOUR_SENTENCES, group=True)) == 2


def test_group_settings_do_not_touch_private():
    """群聊参数设得再狠，私聊结果也不能变。"""
    ctx = _Ctx(**{"output.group_split_chars": 10, "output.group_split_max": 1})
    assert len(_parts(ctx, FOUR_SENTENCES, group=False)) == 4
    assert len(_parts(ctx, FOUR_SENTENCES, group=True)) == 1


def test_split_disabled_still_sends_one():
    ctx = _Ctx(**{"output.split_enabled": False})
    assert len(_parts(ctx, FOUR_SENTENCES, group=True)) == 1
