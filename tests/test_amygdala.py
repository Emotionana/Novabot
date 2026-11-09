"""杏仁核测试。

跑：python -m tests.test_amygdala
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Database  # noqa: E402
from app.models import ChatType, InboundMessage  # noqa: E402


def main() -> int:
    import app.params  # noqa: F401
    from app.memory.amygdala import Amygdala, Judgment
    from app.memory.hippocampus import Hippocampus
    from app.memory.migrate import init_schema

    tmp = Path(tempfile.mkdtemp(prefix="novabot-amyg-"))
    db = Database(tmp / "test.db")
    init_schema(db)
    amy = Amygdala(db)

    print("=== 规则快判")
    cases = [
        ("今天好开心啊", "joy", "正"),
        ("太棒了！", "joy", "正+强调"),
        ("好烦啊", "sadness", "负"),
        ("烦死了！气死我了", "anger", "负+强调"),
        ("我好累", "sadness", "负"),
        ("今天天气不错", "joy", "正"),
        ("嗯", "neutral", "无"),
        ("???", "neutral", "纯问号"),
        ("[CQ:face,id=1]笑", "joy", "表情"),
    ]
    for text, expect_emo, note in cases:
        j = amy.rule_judge(text)
        mark = "✓" if j.emotion == expect_emo else "?"
        print(f"  {mark} {text:18s} → {j.emotion:9s} v={j.valence:+.2f} a={j.arousal:.2f} conf={j.confidence:.2f}  ({note})")

    print()
    print("=== 愤怒 vs 悲伤：都是负，但唤醒不同")
    anger = amy.rule_judge("烦死了！")
    sad = amy.rule_judge("有点难过")
    print(f"  烦死了！  → {anger.emotion:8s} arousal={anger.arousal:.2f}")
    print(f"  有点难过  → {sad.emotion:8s} arousal={sad.arousal:.2f}")
    assert anger.arousal > sad.arousal, "愤怒应该比悲伤高唤醒"

    print()
    print("=== 讽刺")
    j = amy.rule_judge("真好啊，又加班")
    print(f"  规则判：{j.emotion} v={j.valence:+.2f} conf={j.confidence:.2f}")
    print("  （规则判不出讽刺，要靠模型）")

    print()
    print("=== 融合：两边不一致 → 中性")
    rule = Judgment("joy", 0.6, 0.5, 0.7, "rule")
    model = Judgment("sadness", -0.5, 0.4, 0.8, "model")
    fused = amy.fuse(rule, model)
    print(f"  规则 joy + 模型 sadness → {fused.emotion} v={fused.valence:+.2f} conf={fused.confidence:.2f}")
    assert fused.emotion == "neutral", "不一致应该给中性"
    assert fused.confidence <= 0.7, "置信度取低"

    print()
    print("=== 融合：两边一致 → 平均")
    rule2 = Judgment("joy", 0.8, 0.7, 0.7, "rule")
    model2 = Judgment("joy", 0.6, 0.5, 0.9, "model")
    fused2 = amy.fuse(rule2, model2)
    print(f"  两边都 joy → {fused2.emotion} v={fused2.valence:+.2f} a={fused2.arousal:.2f}")
    assert fused2.emotion == "joy"
    assert abs(fused2.valence - 0.7) < 0.01

    print()
    print("=== 落库")
    hippo = Hippocampus(db)
    QQ = "10001"
    texts = ["今天好开心", "但是又好累", "唉", "算了不说了"]
    ep_ids = []
    for t in texts:
        ep_ids.append(
            hippo.write(
                InboundMessage(
                    account_id="default", chat_id="group:1", chat_type=ChatType.GROUP,
                    group_id="1", sender_qq=QQ, sender_nick="TestUser", text=t,
                )
            )
        )
    eps = hippo.get_many(ep_ids)
    judgments = amy.judge_many(eps)
    print(f"  判了 {len(judgments)} 条")
    for ep, j in zip(eps, judgments):
        print(f"    {ep.text:12s} → {j.emotion}")

    # 读回
    got = amy.of_episode(ep_ids[0])
    print(f"  读回第一条：{got.emotion} v={got.valence:+.2f}")
    assert got is not None

    print()
    print("=== 情绪基线")
    v, a, n = amy.baseline(QQ)
    print(f"  平均 valence={v:+.3f} arousal={a:.3f} 样本={n}")
    amy.update_baseline(QQ)
    shift = amy.mood_shift(QQ)
    print(f"  跟基线比偏移：{shift:+.3f}")

    print()
    print("=== 情绪画像")
    prof = amy.profile(QQ)
    print(f"  分布：{prof['distribution']}")
    print(f"  样本：{prof['sample']}  偏移：{prof['shift']}")

    print()
    print("=== 重复判同一条不会重复插入")
    before = db.scalar("SELECT COUNT(*) FROM emotions")
    amy.judge_episode(eps[0])
    after = db.scalar("SELECT COUNT(*) FROM emotions")
    print(f"  判前 {before} 条，判后 {after} 条")
    assert before == after, "应该更新而不是插入"

    db.close()
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
