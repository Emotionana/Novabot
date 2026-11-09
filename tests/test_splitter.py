"""回复拆分：不固定回几句，按语义/长度自然拆。"""

from __future__ import annotations

from app.core.splitter import split_natural


def test_short_reply_stays_one_message():
    assert split_natural("收到。", max_chars=42, max_parts=4) == ["收到。"]
    assert split_natural("嗯。好。", max_chars=42, max_parts=4) == ["嗯。好。"]


def test_multiple_sentences_and_long_clause_split():
    two = split_natural("今天天气不错。我们去散步吧。", max_chars=42, max_parts=4)
    assert len(two) == 2
    long = split_natural("我今天去了趟超市，买了点水果，还顺便把快递拿了，回来的时候天已经黑了。", max_chars=42, max_parts=4)
    assert len(long) >= 2
    assert "".join(long) == "我今天去了趟超市，买了点水果，还顺便把快递拿了，回来的时候天已经黑了。"


def test_explicit_separator_and_link_protection():
    assert split_natural("嗯|||知道了|||你先去", max_chars=42, max_parts=4) == ["嗯", "知道了", "你先去"]
    url = "看这个 https://example.com/a/b 很好。"
    assert split_natural(url, max_chars=42, max_parts=4) == [url]
