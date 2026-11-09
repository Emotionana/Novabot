"""真实性：工作模式识别、不知道不装懂、假称完成拦截。"""

from __future__ import annotations

from app.truth import TruthPolicy


def test_work_mode_owner_private(settings):
    t = TruthPolicy(settings)
    assert t.work_context("帮我修改一下这个文件", is_owner=True, is_private=True) is True
    assert t.work_context("今天天气不错", is_owner=True, is_private=True) is False
    assert t.work_context("帮我修改文件", is_owner=False, is_private=False) is False


def test_prompt_forbids_fake_completion(settings):
    t = TruthPolicy(settings)
    p = t.prompt(work=True)
    assert "不知道" in p
    assert "没有实际执行" in p
    assert "工作模式" in p


def test_review_blocks_action_claim_without_tool_evidence(settings):
    t = TruthPolicy(settings)
    text = "我已经帮你把文件改好了。"
    assert "没有实际执行成功" in t.review(text, work=True, tools={"calls": 0, "results": 0, "errors": 0})
    assert t.review(text, work=True, tools={"calls": 1, "results": 1, "errors": 0}) == text
    assert t.review(text, work=False, tools={}) == text
