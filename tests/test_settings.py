"""参数与设置的冒烟测试。

跑：python -m tests.test_settings
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Database  # noqa: E402


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="novabot-set-"))
    db = Database(tmp / "test.db")

    import app.params  # 触发注册  # noqa: F401
    from app.settings import Settings, all_params, by_group

    params = all_params()
    basic = [p for p in params.values() if p.level == "basic"]
    adv = [p for p in params.values() if p.level == "advanced"]
    print(f"=== 注册 {len(params)} 项（基础 {len(basic)} / 高级 {len(adv)}）")
    assert len(params) >= 90, f"参数太少了：{len(params)}"

    # 每个参数都要有 label 和 desc
    missing = [p.key for p in params.values() if not p.label or not p.desc]
    assert not missing, f"这些参数缺说明：{missing}"
    print("=== 每个参数都有标签和说明")

    # 分组
    groups = by_group()
    print(f"=== {len(groups)} 个分组：{'、'.join(groups)}")

    # 设置读写
    s = Settings(db)
    print()
    print("=== 读默认值")
    assert s.get("reply.group_mode") == "mention"
    assert s.get("memory.recall_chars") == 300
    assert s.get("voice.enabled") is False
    print("  reply.group_mode =", s.get("reply.group_mode"))
    print("  memory.recall_chars =", s.get("memory.recall_chars"))
    print("  voice.enabled =", s.get("voice.enabled"))

    # 改
    print()
    print("=== 改值")
    s.set("reply.group_mode", "smart")
    assert s.get("reply.group_mode") == "smart"
    assert s.is_custom("reply.group_mode")
    print("  改成 smart →", s.get("reply.group_mode"))
    print("  标记为已改 →", s.is_custom("reply.group_mode"))

    # 范围裁剪
    print()
    print("=== 范围校验（自动裁剪）")
    v = s.set("memory.recall_chars", 99999)
    assert v == 3000, f"应该被裁到 3000，实际 {v}"
    print(f"  填 99999 → 裁到 {v}")

    v = s.set("llm.temperature", -5)
    assert v == 0.0, f"应该被裁到 0，实际 {v}"
    print(f"  填 -5 → 裁到 {v}")

    # 枚举校验
    print()
    print("=== 枚举校验")
    try:
        s.set("reply.group_mode", "乱填的")
        raise AssertionError("应该报错")
    except ValueError as e:
        print(f"  填乱值 → 报错：{e}")

    # 布尔
    print()
    print("=== 布尔转换")
    for raw, expect in [("true", True), ("off", False), ("1", True), (0, False)]:
        v = s.set("voice.enabled", raw)
        assert v is expect, f"{raw!r} 应该转成 {expect}，实际 {v}"
    print("  'true'/'off'/'1'/0 都能正确转换")

    # 列表
    print()
    print("=== 列表")
    v = s.set("reply.wake_words", "小助手, 小助, 演示")
    assert v == ["小助手", "小助", "演示"], v
    print(f"  逗号分隔字符串 → {v}")

    # 恢复默认
    print()
    print("=== 恢复默认")
    s.reset("reply.group_mode")
    assert s.get("reply.group_mode") == "mention"
    assert not s.is_custom("reply.group_mode")
    print("  单项恢复 →", s.get("reply.group_mode"))

    s.set("memory.recall_chars", 500)
    s.set("voice.enabled", True)
    s.reset()
    assert s.get("memory.recall_chars") == 300
    assert s.get("voice.enabled") is False
    print("  全部恢复 → 都回到默认")

    # 全部读取
    print()
    allv = s.get_all()
    assert len(allv) == len(params)
    print(f"=== get_all 返回 {len(allv)} 项")

    b = s.get_all(level="basic")
    print(f"=== 只取基础项：{len(b)} 项")

    # 未知参数
    print()
    print("=== 未知参数")
    try:
        s.get("no.such.key")
        raise AssertionError("应该报错")
    except KeyError as e:
        print(f"  报错：{e}")

    db.close()
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())


def test_secret_param_is_masked_and_not_overwritten(settings):
    settings.set("gateway.onebot_token", "real-token")
    assert settings.get("gateway.onebot_token") == "real-token"
    assert settings.get_all()["gateway.onebot_token"] == "••••••"

    # 界面把打码值原样提交回来，不能覆盖真 token
    settings.set("gateway.onebot_token", "••••••")
    assert settings.get("gateway.onebot_token") == "real-token"
