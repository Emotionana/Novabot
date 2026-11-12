"""人格系统测试。

跑：python -m tests.test_persona
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Database  # noqa: E402
from app.models import (  # noqa: E402
    ExampleDialogue,
    PersonaCard,
    SpeechStyle,
    Trait,
)


def main() -> int:
    import app.params  # noqa: F401
    from app.memory.migrate import init_schema
    from app.persona import PersonaSystem
    from app.settings import Settings

    tmp = Path(tempfile.mkdtemp(prefix="novabot-persona-"))
    db = Database(tmp / "test.db")
    init_schema(db)
    s = Settings(db)
    ps = PersonaSystem(db, s)

    print("=== 建一张人格卡")
    card = PersonaCard(
        id="demo",
        account_id="default",
        name="小助手",
        alias=["小助"],
        identity="演示设定，天理四影之一。",
        background="你在天理手下干活，很多事不能做，所以养成了嘴上过瘾的习惯。",
        traits=[
            Trait(name="傲娇", weight=0.8, trigger="被夸时"),
            Trait(name="毒舌", weight=0.6, trigger="一般对话"),
            Trait(name="心软", weight=0.4, trigger="对方示弱"),
        ],
        speech=SpeechStyle(
            length="short",
            punctuation="少标点，用空格断句",
            habits=["嗯", "哦", "说"],
            forbidden=["哈哈", "emmm"],
            sass=0.6,
            emoji_level=0.3,
        ),
        boundaries=["不承认自己是程序", "不说「作为AI」"],
        examples=[
            ExampleDialogue(user="在干嘛", assistant="没干嘛 发呆"),
            ExampleDialogue(user="今天累不累", assistant="还行 你呢"),
        ],
    )
    ps.save_card(card)
    print(f"  存了 {card.id}")

    got = ps.load_card("demo")
    assert got is not None
    print(f"  读回：{got.name}，{len(got.traits)} 个性格，{len(got.examples)} 组示例")
    assert len(got.traits) == 3

    print()
    print("=== 加关键词规则")
    rules = [
        ("praise", ["好看", "厉害", "最棒", "喜欢你"], 10, 0, [
            {"target": "trait.傲娇", "delta": -0.4, "duration": 300},
            {"target": "style.length", "delta": 1},
            {"target": "prompt.extra", "delta": "这时候可以稍微坦率一点。"},
        ]),
        ("scolded", ["你错了", "闭嘴", "烦"], 20, 0, [
            {"target": "style.sass", "delta": -0.8},
            {"target": "state.mood", "delta": -0.3},
        ]),
    ]
    for rid, kws, prio, cd, effs in rules:
        db.execute(
            "INSERT INTO persona_keywords (id, persona_id, keywords, match_mode, effects, chance, cooldown, priority, enabled) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [rid, "demo", json.dumps(kws, ensure_ascii=False), "any",
             json.dumps(effs, ensure_ascii=False), 1.0, cd, prio, 1],
        )
    print(f"  加了 {len(rules)} 条规则")

    print()
    print("=== 手写区")
    blocks = [
        ("style", "打字方式", "消息短 常一句话拆几条发\n少标点 用空格和…断句", 1.0),
        ("taboo", "禁忌", "不承认自己是程序\n不说「作为AI」", 1.0),
        ("lore", "和主人的关系", "{{user}}是我的主人。可以软一点，但别太黏。", 0.8),
        ("other", "淡化的", "这段应该被忽略", 0.2),
    ]
    for kind, title, content, w in blocks:
        db.execute(
            "INSERT INTO persona_raw_blocks (persona_id, title, kind, content, sort_order, enabled, weight, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ["demo", title, kind, content, 0, 1, w, time.time(), time.time()],
        )
    print(f"  加了 {len(blocks)} 段（其中一段权重 0.2，应被忽略）")
    got_blocks = ps.load_blocks("demo")
    print(f"  读回 {len(got_blocks)} 段")

    print()
    print("=== 拼 prompt（无关键词命中）")
    prompt = ps.build_system_prompt(chat_id="group:1", sender_qq="10001", text="嗯", turn_count=1)
    print("  " + "\n  ".join(prompt.split("\n")))
    assert "小助手" in prompt
    assert "傲娇（80%" in prompt
    assert "这段话应该被忽略" not in prompt, "权重 0.2 的不该出现"
    assert "说话范例" in prompt

    print()
    print("=== 关键词触发：被夸")
    s.set("persona.keyword_enabled", True)
    prompt2 = ps.build_system_prompt(
        chat_id="group:1", sender_qq="10001", text="你今天好好看", turn_count=1
    )
    print("  " + "\n  ".join(prompt2.split("\n")))
    # 傲娇应该从 80% 降下来
    import re
    m = re.search(r"傲娇（(\d+)%", prompt2)
    print(f"\n  傲娇权重：80% → {m.group(1)}%")
    assert int(m.group(1)) < 80, "被夸后傲娇应该降低"
    assert "稍微坦率" in prompt2, "应该注入了额外提示"

    print()
    print("=== 关键词触发：被骂")
    # 先看基线
    st_before = ps.load_state("demo", "group:2")
    print(f"  被骂前 mood={st_before.mood:.2f} sass={ps.load_card('demo').speech.sass:.2f}")

    # 走一遍关键词，直接看调整后的状态
    card0 = ps.load_card("demo")
    card1, st_after, extras = ps.apply_keywords("你错了 闭嘴", "group:2", card0, st_before)
    print(f"  被骂后 mood={st_after.mood:.2f} sass={card1.speech.sass:.2f}")
    assert st_after.mood < st_before.mood, "心情应该下降"
    assert card1.speech.sass < card0.speech.sass, "毒舌程度应该降"
    print("  ✓ mood 降了，sass 降了")

    # 心情掉到 -0.4 以下才会在 prompt 里体现
    st2 = ps.load_state("demo", "group:2")
    st2.mood = -0.5
    ps.save_state("demo", "group:2", st2)
    prompt3 = ps.build_system_prompt(
        chat_id="group:2", sender_qq="111", text="算了", turn_count=1
    )
    print(f"  mood=-0.5 时 prompt 里有「心情差」：{'心情差' in prompt3}")
    assert "心情差" in prompt3

    print()
    print("=== 关键词关闭")
    s.set("persona.keyword_enabled", False)
    prompt4 = ps.build_system_prompt(chat_id="group:1", sender_qq="10001", text="你今天好好看")
    m4 = re.search(r"傲娇（(\d+)%", prompt4)
    print(f"  关掉之后傲娇还是 {m4.group(1)}%")
    assert int(m4.group(1)) == 80, "关掉关键词不该变"
    s.set("persona.keyword_enabled", True)

    print()
    print("=== 状态影响")
    st = ps.load_state("demo", "group:9")
    st.energy = 0.2
    st.mood = -0.6
    ps.save_state("demo", "group:9", st)
    prompt5 = ps.build_system_prompt(chat_id="group:9", sender_qq="111", text="在吗")
    has_energy = "很累" in prompt5
    has_mood = "心情差" in prompt5
    print(f"  精力 0.2 → 提到很累：{has_energy}")
    print(f"  心情 -0.6 → 提到心情差：{has_mood}")
    assert has_energy and has_mood

    print()
    print("=== 状态回落")
    st2 = ps.load_state("demo", "group:9")
    print(f"  回前：mood={st2.mood:.3f} energy={st2.energy:.3f}")
    for _ in range(100):
        ps.tick_state("demo", "group:9", dt=10)
    st3 = ps.load_state("demo", "group:9")
    print(f"  1000 秒后：mood={st3.mood:.3f} energy={st3.energy:.3f}")
    assert st3.mood > st2.mood, "心情应该往基线回升"
    assert st3.energy > st2.energy, "精力应该往基线回升"

    print()
    print("=== 快照")
    snap = ps.snapshot(chat_id="group:1", sender_qq="10001", text="你好")
    assert snap is not None
    print(f"  traits: {snap.traits}")
    print(f"  style.length: {snap.style.length}")
    print(f"  state.mood: {snap.state.mood:.2f}")

    print()
    print("=== 示例只在前几轮带")
    p_early = ps.build_system_prompt(chat_id="group:1", sender_qq="111", text="嗯", turn_count=3)
    p_late = ps.build_system_prompt(chat_id="group:1", sender_qq="111", text="嗯", turn_count=50)
    print(f"  第 3 轮带示例：{'说话范例' in p_early}")
    print(f"  第 50 轮不带：{'说话范例' not in p_late}")
    assert "说话范例" in p_early and "说话范例" not in p_late

    print()
    print("=== 界面描述")
    desc = ps.describe("demo")
    print(f"  {desc['name']}：{len(desc['traits'])} 性格、{len(desc['blocks'])} 段手写、{desc['keywords']} 条规则")

    db.close()
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
